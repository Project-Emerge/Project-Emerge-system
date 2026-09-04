from dataclasses import replace

import cv2
import numpy as np

from vision_system.core.config import FusionConfig
from vision_system.core.geometry import invert_transform, marker_object_points, pose_matrix
from vision_system.pipeline.detection import TagObservation
from vision_system.pipeline.fusion import FusionEngine


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
        reprojection_error_px=0.0,
        marker_side_px=72.0,
    )


def move_observation(
    observation: TagObservation, position: tuple[float, float, float], timestamp: int
) -> TagObservation:
    world_from_tag = observation.world_from_tag.copy()
    world_from_tag[:3, 3] = position
    return replace(
        observation,
        monotonic_ns=timestamp,
        utc_ns=timestamp,
        world_from_tag=world_from_tag,
        camera_from_tag=invert_transform(observation.world_from_camera) @ world_from_tag,
    )


def test_two_camera_fusion_recovers_world_pose() -> None:
    engine = FusionEngine(FusionConfig())
    pose = engine.fuse(
        [
            make_observation("cam_0", 0.0, 1_000_000_000),
            make_observation("cam_1", 0.5, 1_010_000_000),
        ]
    )
    assert pose is not None
    np.testing.assert_allclose(pose.position_m, [0.1, -0.05, 2.0], atol=1e-6)
    assert pose.cameras == ["cam_0", "cam_1"]
    assert pose.quality > 0.9


def test_large_innovation_is_clamped() -> None:
    engine = FusionEngine(FusionConfig(tracker_max_innovation_m=0.15))
    first = make_observation("cam_0", 0.0, 1_000_000_000)
    pose = engine.fuse([first])
    assert pose is not None
    shifted_transform = first.world_from_tag.copy()
    shifted_transform[:3, 3] += np.array([0.5, 0.0, 0.0])
    shifted = replace(first, monotonic_ns=1_050_000_000, utc_ns=1_050_000_000)
    shifted.world_from_tag = shifted_transform
    updated = engine.fuse([shifted])
    assert updated is not None
    assert 0.0 < updated.position_m[0] - 0.1 < 0.1


def test_predict_repeats_pose_within_stale_window() -> None:
    config = FusionConfig()
    engine = FusionEngine(config)
    engine.fuse([make_observation("cam_0", 0.0, 1_000_000_000)])
    pose = engine.predict(23, 1_000_400_000, 1_000_400_000)
    assert pose is not None
    assert pose.predicted
    assert pose.quality == 0.0
    assert pose.cameras == []
    np.testing.assert_allclose(pose.position_m, [0.1, -0.05, 2.0], atol=1e-6)
    expired = engine.predict(
        23, 1_000_000_000 + int(config.stale_after_ms * 1e6) + 1, 0
    )
    assert expired is None


def test_one_euro_filter_is_more_responsive_during_fast_motion() -> None:
    initial_timestamp = 1_000_000_000
    initial = make_observation("cam_0", 0.0, initial_timestamp)
    fixed_cutoff = FusionEngine(
        FusionConfig(
            tracker_max_innovation_m=0,
            one_euro_min_cutoff_hz=2.0,
            one_euro_beta=0.0,
        )
    )
    adaptive = FusionEngine(
        FusionConfig(
            tracker_max_innovation_m=0,
            one_euro_min_cutoff_hz=2.0,
            one_euro_beta=5.0,
        )
    )
    fixed_cutoff.fuse([initial])
    adaptive.fuse([initial])
    moved = move_observation(initial, (0.4, -0.05, 2.0), initial_timestamp + 50_000_000)

    fixed_pose = fixed_cutoff.fuse([moved])
    adaptive_pose = adaptive.fuse([moved])

    assert fixed_pose is not None
    assert adaptive_pose is not None
    assert adaptive_pose.position_m[0] - 0.1 > fixed_pose.position_m[0] - 0.1


def test_one_euro_velocity_settles_instead_of_accumulating() -> None:
    initial_timestamp = 1_000_000_000
    initial = make_observation("cam_0", 0.0, initial_timestamp)
    engine = FusionEngine(FusionConfig(tracker_max_innovation_m=0))
    engine.fuse([initial])

    pose = None
    for index in range(1, 6):
        timestamp = initial_timestamp + index * 50_000_000
        pose = engine.fuse(
            [move_observation(initial, (0.1 + index * 0.08, -0.05, 2.0), timestamp)]
        )
    for index in range(6, 31):
        timestamp = initial_timestamp + index * 50_000_000
        pose = engine.fuse([move_observation(initial, (0.5, -0.05, 2.0), timestamp)])

    assert pose is not None
    assert abs(pose.velocity_m_s[0]) < 0.02
    assert abs(pose.position_m[0] - 0.5) < 0.01
