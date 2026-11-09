from pathlib import Path
from time import sleep, time_ns

import numpy as np

from vision_system.apps.coordinator import VisionCoordinator
from vision_system.core.config import AppConfig, CameraCalibration, save_json
from vision_system.core.geometry import invert_transform, marker_object_points, pose_matrix
from vision_system.pipeline.detection import TagObservation
from vision_system.transport.observations import observations_payload


def _write_calibration(directory: Path, camera_id: str, source: int | str, camera_x: float) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    world_from_camera = pose_matrix(np.array([camera_x, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    save_json(
        directory / f"{camera_id}.json",
        CameraCalibration(
            camera_id=camera_id,
            source=source,
            image_size=(1920, 1080),
            camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
            distortion=[0.0] * 8,
            world_from_camera=world_from_camera.tolist(),
            intrinsic_median_error_px=0.2,
            intrinsic_p95_error_px=0.4,
            captured_at="2026-08-18T00:00:00+00:00",
            opencv_version="test",
            board_checksum="test",
        ),
    )


def _make_observation(camera_id: str, camera_x: float, timestamp: int) -> TagObservation:
    import cv2

    world_from_camera = pose_matrix(np.array([camera_x, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    world_from_tag = pose_matrix(np.array([0.1, -0.05, 2.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    camera_from_tag = invert_transform(world_from_camera) @ world_from_tag
    matrix = np.array([[1200.0, 0, 960], [0, 1200.0, 540], [0, 0, 1]])
    distortion = np.zeros(8)
    object_points = marker_object_points(0.12)
    rvec, _ = cv2.Rodrigues(camera_from_tag[:3, :3])
    corners, _ = cv2.projectPoints(object_points, rvec, camera_from_tag[:3, 3], matrix, distortion)
    return TagObservation(
        tag_id=23,
        camera_id=camera_id,
        monotonic_ns=timestamp,
        utc_ns=timestamp,
        corners=corners.reshape(4, 2),
        camera_from_tag=camera_from_tag,
        world_from_tag=world_from_tag,
        world_from_camera=world_from_camera,
        camera_matrix=matrix,
        distortion=distortion,
        object_points=object_points,
        reprojection_error_px=0.2,
        marker_side_px=72.0,
        candidate_camera_from_tags=(camera_from_tag,),
    )


def test_coordinator_fuses_observations_from_two_nodes(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    _write_calibration(calibration_dir, "cam_1", 1, camera_x=0.5)
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    timestamp = time_ns()
    for camera_id, camera_x in (("cam_0", 0.0), ("cam_1", 0.5)):
        observation = _make_observation(camera_id, camera_x, timestamp)
        coordinator.message_queue.put(
            observations_payload(camera_id, timestamp, [observation])
        )
    poses = coordinator._tick()
    assert len(poses) == 1
    np.testing.assert_allclose(poses[0].position_m, [0.1, -0.05, 2.0], atol=1e-6)
    assert poses[0].cameras == ["cam_0", "cam_1"]
    assert len(published) == 1
    assert published[0].tag_id == 23


def test_coordinator_drops_observations_without_calibration(tmp_path: Path) -> None:
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=tmp_path / "calibrations",
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    observation = _make_observation("cam_0", 0.0, time_ns())
    coordinator.message_queue.put(
        observations_payload("cam_0", observation.utc_ns, [observation])
    )
    assert coordinator._tick() == []
    assert not published
    assert coordinator.diagnostic_observations["missing_calibration"] == 1


def test_coordinator_rejects_invalid_messages(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    coordinator.message_queue.put({"schema_version": 1, "camera_id": "cam_0", "utc_ns": "NaN"})
    assert coordinator._tick() == []
    assert coordinator.diagnostic_observations["invalid_message"] == 1
    coordinator.message_queue.put({"schema_version": 99, "camera_id": "cam_0", "utc_ns": 0})
    assert coordinator._tick() == []
    assert coordinator.diagnostic_observations["invalid_message"] == 2


def test_coordinator_respects_fusion_window(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    timestamp = time_ns()
    observation = _make_observation("cam_0", 0.0, timestamp)
    coordinator.message_queue.put(observations_payload("cam_0", timestamp, [observation]))
    poses = coordinator._tick()
    assert len(poses) == 1
    far_later = timestamp + int(coordinator.config.fusion.window_ms * 1e6 * 10)
    observation = _make_observation("cam_0", 0.0, far_later)
    coordinator.message_queue.put(observations_payload("cam_0", far_later, [observation]))
    poses = coordinator._tick()
    assert len(poses) == 1
    assert poses[0].utc_ns == far_later


def test_coordinator_pose_publish_rate_limited(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    timestamp = time_ns()
    interval_ns = int(1e9 / coordinator.config.fusion.publish_hz)
    for index in range(4):
        now = timestamp + index * (interval_ns // 3)
        observation = _make_observation("cam_0", 0.0, now)
        coordinator.message_queue.put(observations_payload("cam_0", now, [observation]))
        coordinator._tick()
    assert 1 <= len(published) <= 2
    assert coordinator.pose_count == len(published)


def test_coordinator_publishes_predicted_pose_while_tag_unseen(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    coordinator = VisionCoordinator(
        AppConfig(),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    timestamp = time_ns()
    observation = _make_observation("cam_0", 0.0, timestamp)
    coordinator.message_queue.put(observations_payload("cam_0", timestamp, [observation]))
    coordinator._tick()
    assert len(published) == 1
    assert not published[0].predicted
    coordinator.observation_window.last_publish_ns[23] = 0
    coordinator._tick()
    assert len(published) == 2
    assert published[1].predicted
    assert published[1].quality == 0.0


def test_coordinator_stops_predicting_after_stale_window(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_0", 5, camera_x=0.0)
    coordinator = VisionCoordinator(
        AppConfig(fusion={"stale_after_ms": 10.0}),
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    published = []
    coordinator.bridge.publish_pose = lambda pose: published.append(pose)
    timestamp = time_ns()
    observation = _make_observation("cam_0", 0.0, timestamp)
    coordinator.message_queue.put(observations_payload("cam_0", timestamp, [observation]))
    coordinator._tick()
    assert len(published) == 1
    sleep(0.03)
    coordinator.observation_window.last_publish_ns[23] = 0
    coordinator._tick()
    assert len(published) == 1
