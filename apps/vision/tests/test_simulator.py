import numpy as np
import pytest

from vision_system.apps.simulator import (
    default_target_size,
    observations_payload,
    path_extent,
    project_marker,
    reference_marker_poses,
    select_cameras,
    target_marker_pose,
)
from vision_system.core.config import (
    AppConfig,
    CameraConfig,
    MobileMarkerConfig,
    ReferenceMarkerConfig,
)
from vision_system.core.geometry import invert_transform, pose_matrix

CAMERA_MATRIX = np.array([[600.0, 0.0, 960.0], [0.0, 600.0, 540.0], [0.0, 0.0, 1.0]])
DISTORTION = np.zeros(8)


def _look_at(position: np.ndarray, target: np.ndarray) -> np.ndarray:
    forward = target - position
    forward = forward / np.linalg.norm(forward)
    right = np.cross(np.array([0.0, 0.0, 1.0]), forward)
    if np.linalg.norm(right) < 1e-6:
        right = np.array([1.0, 0.0, 0.0])
    right = right / np.linalg.norm(right)
    up = np.cross(forward, right)
    transform = np.eye(4)
    transform[:3, :3] = np.column_stack((right, up, forward))
    transform[:3, 3] = position
    return transform


def _rng() -> np.random.Generator:
    return np.random.default_rng(7)


def test_project_marker_visible_marker_produces_observation() -> None:
    world_from_camera = _look_at(np.array([0.5, -0.5, 1.2]), np.array([0.0, 0.0, 0.0]))
    camera_from_world = invert_transform(world_from_camera)
    world_from_tag = pose_matrix(np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    item = project_marker(
        23,
        world_from_tag,
        0.07,
        camera_from_world,
        world_from_camera,
        CAMERA_MATRIX,
        DISTORTION,
        (1920, 1080),
        _rng(),
        0.5,
    )
    assert item is not None
    assert item["tag_id"] == 23
    assert np.asarray(item["corners"]).shape == (4, 2)
    assert np.allclose(
        np.asarray(item["camera_from_tag"]), (camera_from_world @ world_from_tag)[:3, :]
    )
    assert item["marker_side_px"] > 0
    assert item["reprojection_error_px"] >= 0
    corners = np.asarray(item["corners"])
    assert (corners[:, 0] > 0).all() and (corners[:, 0] < 1920).all()
    assert (corners[:, 1] > 0).all() and (corners[:, 1] < 1080).all()


def test_project_marker_rejects_backfacing_and_behind() -> None:
    world_from_camera = _look_at(np.array([0.5, -0.5, 1.2]), np.array([0.0, 0.0, 0.0]))
    camera_from_world = invert_transform(world_from_camera)
    flipped = pose_matrix(np.array([0.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0, 0.0]))
    assert (
        project_marker(
            23,
            flipped,
            0.07,
            camera_from_world,
            world_from_camera,
            CAMERA_MATRIX,
            DISTORTION,
            (1920, 1080),
            _rng(),
            0.5,
        )
        is None
    )
    behind = world_from_camera @ pose_matrix(
        np.array([0.0, 0.0, -2.0]), np.array([0.0, 0.0, 0.0, 1.0])
    )
    assert (
        project_marker(
            23,
            behind,
            0.07,
            camera_from_world,
            world_from_camera,
            CAMERA_MATRIX,
            DISTORTION,
            (1920, 1080),
            _rng(),
            0.5,
        )
        is None
    )


def test_observations_payload_matches_wire_format() -> None:
    assert observations_payload("cam_0", 123, []) == {
        "schema_version": 1,
        "camera_id": "cam_0",
        "utc_ns": 123,
        "observations": [],
    }


def test_reference_marker_poses_roundtrip() -> None:
    config = AppConfig(
        aruco={
            "reference_markers": [
                ReferenceMarkerConfig(
                    id=13,
                    size_m=0.07,
                    position_m=(0.0, 0.0, 0.0),
                    orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
                ),
                ReferenceMarkerConfig(
                    id=15,
                    size_m=0.08,
                    position_m=(1.1, 0.0, 0.0),
                    orientation_xyzw=(0.0, 0.0, 1.0, 0.0),
                ),
            ]
        }
    )
    poses = reference_marker_poses(config)
    assert [tag_id for tag_id, _, _ in poses] == [13, 15]
    assert [size for _, size, _ in poses] == [0.07, 0.08]
    for tag_id, _size_m, transform in poses:
        reference = config.references_by_id()[tag_id]
        assert np.allclose(transform[:3, 3], np.asarray(reference.position_m))


def test_target_marker_pose_is_static_at_extent_center() -> None:
    config = AppConfig()
    center, radius = path_extent(config)
    assert np.allclose(center, np.array([0.55, 0.55, 0.0]))
    pose = target_marker_pose(config)
    assert np.allclose(pose[:3, 3], center)
    assert np.allclose(pose[:3, :3], np.eye(3))


def test_select_cameras_by_id_source_and_default() -> None:
    config = AppConfig(
        cameras=[
            CameraConfig(id="cam_0", source=3),
            CameraConfig(id="cam_1", source=4),
            CameraConfig(id="cam_2", source=6),
            CameraConfig(id="cam_3", source="/dev/video9"),
        ]
    )
    assert [camera.id for camera in select_cameras(config)] == [
        "cam_0",
        "cam_1",
        "cam_2",
        "cam_3",
    ]
    assert [camera.id for camera in select_cameras(config, ["cam_1", "cam_3"])] == [
        "cam_1",
        "cam_3",
    ]
    assert [camera.id for camera in select_cameras(config, ["4", "6"])] == ["cam_1", "cam_2"]
    assert [camera.id for camera in select_cameras(config, ["/dev/video9"])] == ["cam_3"]
    with pytest.raises(ValueError):
        select_cameras(config, ["cam_9"])


def test_default_target_size_prefers_auto_mobile_markers() -> None:
    config = AppConfig(
        aruco={
            "auto_mobile_markers": {"enabled": True, "default_size_m": 0.07},
            "mobile_markers": [MobileMarkerConfig(id=23, size_m=0.12)],
        }
    )
    assert default_target_size(config) == 0.07
    config = AppConfig(
        aruco={"mobile_markers": [MobileMarkerConfig(id=23, size_m=0.12)]}
    )
    assert default_target_size(config) == 0.12
    assert default_target_size(AppConfig()) == 0.07


def test_projection_matches_opencv_without_noise() -> None:
    import cv2

    world_from_camera = _look_at(np.array([0.5, -0.5, 1.2]), np.array([0.0, 0.0, 0.0]))
    camera_from_world = invert_transform(world_from_camera)
    world_from_tag = pose_matrix(np.array([0.1, 0.1, 0.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    camera_from_tag = camera_from_world @ world_from_tag
    rvec, _ = cv2.Rodrigues(camera_from_tag[:3, :3])
    from vision_system.core.geometry import marker_object_points

    expected, _ = cv2.projectPoints(
        marker_object_points(0.07), rvec, camera_from_tag[:3, 3], CAMERA_MATRIX, DISTORTION
    )
    item = project_marker(
        23,
        world_from_tag,
        0.07,
        camera_from_world,
        world_from_camera,
        CAMERA_MATRIX,
        DISTORTION,
        (1920, 1080),
        _rng(),
        0.0,
    )
    assert item is not None
    assert np.allclose(np.asarray(item["corners"]), expected.reshape(4, 2), atol=1e-6)
