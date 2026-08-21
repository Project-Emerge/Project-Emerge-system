import pytest

from vision_system.apps.camera_selector import build_camera_config
from vision_system.core.config import AppConfig, ArucoConfig, CameraConfig, MobileMarkerConfig


def test_camera_selection_preserves_config_and_increments_revision() -> None:
    base = AppConfig(
        revision=7,
        site="factory",
        aruco=ArucoConfig(mobile_markers=[MobileMarkerConfig(id=23, size_m=0.12)]),
    )
    result = build_camera_config(base, {0: 8, 1: 6, 2: 4, 3: 2})
    assert result.revision == 8
    assert result.site == "factory"
    assert result.mobile_marker_sizes() == {23: 0.12}
    assert [camera.source for camera in result.cameras] == [8, 6, 4, 2]


def test_all_logical_cameras_must_be_assigned() -> None:
    with pytest.raises(ValueError, match="all 4"):
        build_camera_config(AppConfig(), {0: 5, 1: 1, 2: 2})


def test_three_camera_selection_updates_all_slots() -> None:
    base = AppConfig(
        cameras=[
            CameraConfig(id="cam_0", source=5),
            CameraConfig(id="cam_1", source=1),
            CameraConfig(id="cam_2", source=2),
        ],
        revision=7,
    )
    result = build_camera_config(base, {0: 8, 1: 6, 2: 4})
    assert [camera.source for camera in result.cameras] == [8, 6, 4]
    assert result.revision == 8
    with pytest.raises(ValueError, match="all 3"):
        build_camera_config(base, {0: 8, 1: 6})


def test_sources_must_be_unique() -> None:
    with pytest.raises(ValueError, match="different"):
        build_camera_config(AppConfig(), {0: 5, 1: 5, 2: 2, 3: 4})


def test_single_camera_selection_updates_only_one_slot() -> None:
    base = AppConfig(revision=3, site="factory")
    result = build_camera_config(base, {2: 9}, only_index=2)
    assert result.revision == 4
    assert result.site == "factory"
    assert [camera.source for camera in result.cameras] == [5, 1, 9, 4]


def test_single_camera_selection_requires_exactly_that_slot() -> None:
    with pytest.raises(ValueError, match="cam_2"):
        build_camera_config(AppConfig(), {1: 3}, only_index=2)
    with pytest.raises(ValueError, match="cam_2"):
        build_camera_config(AppConfig(), {0: 3, 2: 4}, only_index=2)
