import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_system.apps.node import VisionNode
from vision_system.core.config import AppConfig, CameraCalibration, save_json
from vision_system.core.geometry import invert_transform, marker_object_points, pose_matrix
from vision_system.pipeline.detection import TagObservation
from vision_system.transport.mqtt import OfflineBridge
from vision_system.transport.observations import observations_payload


def make_observation(camera_id: str, camera_x: float, timestamp: int) -> TagObservation:
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


def test_observations_payload_is_compact_json() -> None:
    observation = make_observation("cam_0", 0.0, 1_700_000_000_000_000_000)
    payload = observations_payload("cam_0", observation.utc_ns, [observation])
    body = json.loads(json.dumps(payload, default=float))
    assert body["schema_version"] == 1
    assert body["camera_id"] == "cam_0"
    assert body["utc_ns"] == observation.utc_ns
    assert len(body["observations"]) == 1
    item = body["observations"][0]
    assert item["tag_id"] == 23
    assert item["size_m"] == pytest.approx(0.12)
    assert np.asarray(item["corners"]).shape == (4, 2)
    assert np.asarray(item["camera_from_tag"]).shape == (3, 4)
    assert len(item["candidates"]) == 1
    assert item["reprojection_error_px"] == pytest.approx(0.2)
    assert item["marker_side_px"] == pytest.approx(72.0)


def test_node_rejects_unknown_camera(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cam_7"):
        VisionNode(
            AppConfig(),
            "cam_7",
            calibration_dir=tmp_path / "calibrations",
            cache_path=tmp_path / "cache.json",
            mqtt_enabled=False,
        )


def test_node_constructs_offline_bridge_and_own_camera(tmp_path: Path) -> None:
    config = AppConfig()
    node = VisionNode(
        config,
        "cam_2",
        calibration_dir=tmp_path / "calibrations",
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
        print_observations=True,
    )
    assert isinstance(node.bridge, OfflineBridge)
    assert node.bridge.print_observations
    assert node.camera.id == "cam_2"
    assert node.camera.source == 2
    assert node.capture is not None
    assert list(node.capture.workers) == ["cam_2"]


def _write_calibration(directory: Path, camera_id: str, source: int | str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    save_json(
        directory / f"{camera_id}.json",
        CameraCalibration(
            camera_id=camera_id,
            source=source,
            image_size=(1920, 1080),
            camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
            distortion=[0.0] * 8,
            intrinsic_median_error_px=0.2,
            intrinsic_p95_error_px=0.4,
            captured_at="2026-08-18T00:00:00+00:00",
            opencv_version="test",
            board_checksum="test",
        ),
    )


def test_node_builds_detector_only_with_compatible_calibration(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "calibrations"
    _write_calibration(calibration_dir, "cam_2", 2)
    node = VisionNode(
        AppConfig(),
        "cam_2",
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    assert node.detector is not None
    wrong_size = AppConfig()
    wrong_size.cameras[2].width = 1280
    node = VisionNode(
        wrong_size,
        "cam_2",
        calibration_dir=calibration_dir,
        cache_path=tmp_path / "cache.json",
        mqtt_enabled=False,
    )
    assert node.detector is None
