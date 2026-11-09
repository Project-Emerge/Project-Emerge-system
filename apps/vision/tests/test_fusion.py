from dataclasses import replace

import cv2
import numpy as np

from vision_system.core.config import FusionConfig
from vision_system.core.geometry import invert_transform, marker_object_points, pose_matrix
from vision_system.pipeline.detection import TagObservation
from vision_system.pipeline.fusion import FusionEngine
from vision_system.pipeline.fusion_window import ObservationWindow


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


def misaligned_observation(
    observation: TagObservation, offset_m: tuple[float, float, float]
) -> TagObservation:
    """A camera with stale extrinsics: identical pixels, wrong world mapping."""
    world_from_camera = observation.world_from_camera.copy()
    world_from_camera[:3, 3] += np.asarray(offset_m, dtype=float)
    return replace(
        observation,
        world_from_camera=world_from_camera,
        world_from_tag=world_from_camera @ observation.camera_from_tag,
    )


def test_camera_that_contradicts_the_others_is_dropped() -> None:
    engine = FusionEngine(FusionConfig())
    rogue = misaligned_observation(
        make_observation("cam_2", -0.5, 1_000_000_000), (3.0, 0.0, 0.0)
    )
    pose = engine.fuse(
        [
            make_observation("cam_0", 0.0, 1_000_000_000),
            make_observation("cam_1", 0.5, 1_000_000_000),
            rogue,
        ]
    )
    assert pose is not None
    assert pose.cameras == ["cam_0", "cam_1"]
    assert pose.rejected_cameras == ["cam_2"]
    assert pose.camera_disagreement_m > 1.0
    np.testing.assert_allclose(pose.position_m, [0.1, -0.05, 2.0], atol=1e-6)


def test_a_contradicting_camera_cannot_drag_the_fused_pose() -> None:
    """The regression behind 'each camera is steady but the fusion shakes'."""
    engine = FusionEngine(FusionConfig())
    positions = []
    for index, offset in enumerate([0.0, 3.0, -4.0, 7.0, -2.0, 5.0]):
        timestamp = 1_000_000_000 + index * 50_000_000
        pose = engine.fuse(
            [
                make_observation("cam_0", 0.0, timestamp),
                make_observation("cam_1", 0.5, timestamp),
                misaligned_observation(
                    make_observation("cam_2", -0.5, timestamp), (offset, 0.0, 0.0)
                ),
            ]
        )
        assert pose is not None
        positions.append(pose.position_m)
    spread = np.ptp(np.array(positions), axis=0)
    assert np.all(spread < 1e-3), spread


def test_occluding_a_camera_does_not_move_the_fused_pose() -> None:
    engine = FusionEngine(FusionConfig())
    seen_by_all = None
    for index in range(6):
        timestamp = 1_000_000_000 + index * 50_000_000
        seen_by_all = engine.fuse(
            [
                make_observation("cam_0", 0.0, timestamp),
                make_observation("cam_1", 0.5, timestamp),
                make_observation("cam_2", -0.5, timestamp),
            ]
        )
    occluded = engine.fuse(
        [
            make_observation("cam_0", 0.0, 1_300_000_000),
            make_observation("cam_1", 0.5, 1_300_000_000),
        ]
    )
    assert seen_by_all is not None and occluded is not None
    assert occluded.cameras == ["cam_0", "cam_1"]
    assert np.linalg.norm(occluded.position_m - seen_by_all.position_m) < 1e-3


def test_fusion_is_discarded_when_no_rigid_pose_explains_every_camera() -> None:
    engine = FusionEngine(
        FusionConfig(max_camera_disagreement_m=10.0, max_fused_reprojection_error_px=1.0)
    )
    pose = engine.fuse(
        [
            make_observation("cam_0", 0.0, 1_000_000_000),
            misaligned_observation(
                make_observation("cam_1", 0.5, 1_000_000_000), (0.3, 0.0, 0.0)
            ),
        ]
    )
    assert pose is None


def test_pairwise_disagreement_keeps_the_closest_view() -> None:
    engine = FusionEngine(FusionConfig())
    near = make_observation("cam_0", 0.0, 1_000_000_000)
    far = replace(
        misaligned_observation(
            make_observation("cam_1", 0.5, 1_000_000_000), (2.0, 0.0, 0.0)
        ),
        marker_side_px=20.0,
    )
    pose = engine.fuse([near, far])
    assert pose is not None
    assert pose.cameras == ["cam_0"]
    assert pose.rejected_cameras == ["cam_1"]
    np.testing.assert_allclose(pose.position_m, [0.1, -0.05, 2.0], atol=1e-6)


def test_rejected_fusion_falls_back_to_dead_reckoning() -> None:
    """A rejected tag must coast, not go silent, until the cameras agree again."""
    config = FusionConfig(
        max_camera_disagreement_m=10.0, max_fused_reprojection_error_px=1.0
    )
    window = ObservationWindow(FusionEngine(config))
    window.add(make_observation("cam_0", 0.0, 1_000_000_000))
    poses, failed = window.fuse({23})
    assert len(poses) == 1 and not failed

    window.add(make_observation("cam_0", 0.0, 1_010_000_000))
    window.add(
        misaligned_observation(
            make_observation("cam_1", 0.5, 1_010_000_000), (0.3, 0.0, 0.0)
        )
    )
    poses, failed = window.fuse({23})
    assert not poses
    assert failed == {23}

    predicted = window.predict({23} - failed, 1_010_000_000, 1_010_000_000)
    assert [pose.tag_id for pose in predicted] == [23]
    assert predicted[0].predicted
    np.testing.assert_allclose(predicted[0].position_m, [0.1, -0.05, 2.0], atol=1e-6)
