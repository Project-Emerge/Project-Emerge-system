import cv2
import numpy as np
import pytest

from vision_system.apps.camera_configurator import build_camera_settings_config
from vision_system.core.config import AppConfig, CameraConfig
from vision_system.pipeline.capture import apply_camera_properties, apply_digital_zoom


class FakeCapture:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float]] = []

    def set(self, property_id: int, value: float) -> bool:
        self.calls.append((property_id, value))
        return True


def test_camera_settings_preserve_config_and_increment_revision() -> None:
    base = AppConfig(revision=4, site="factory")
    zooms = {f"cam_{index}": 1.0 + index * 0.1 for index in range(4)}
    result = build_camera_settings_config(base, zooms)
    assert result.revision == 5
    assert result.site == "factory"
    assert {camera.id: camera.digital_zoom for camera in result.cameras} == zooms


def test_cameras_not_tuned_here_keep_their_field_of_view() -> None:
    """On a PC owning two of four webcams the others are not open to be measured."""
    result = build_camera_settings_config(AppConfig(), {"cam_1": 2.0})
    assert [camera.digital_zoom for camera in result.cameras] == [1.0, 2.0, 1.0, 1.0]
    assert result.revision == 1


def test_unknown_and_empty_zoom_settings_are_rejected() -> None:
    with pytest.raises(ValueError, match="cam_9"):
        build_camera_settings_config(AppConfig(), {"cam_9": 2.0})
    with pytest.raises(ValueError, match="no camera"):
        build_camera_settings_config(AppConfig(), {})


def test_zoom_is_applied_to_capture() -> None:
    capture = FakeCapture()
    accepted = apply_camera_properties(
        capture,
        CameraConfig(id="cam_0", source=5, zoom=130),
    )
    assert accepted["zoom"] is True
    assert (cv2.CAP_PROP_ZOOM, 130) in capture.calls


def test_digital_zoom_center_crops_and_preserves_size() -> None:
    horizontal = np.tile(np.arange(100, dtype=np.uint8), (60, 1))
    image = np.dstack([horizontal, horizontal, horizontal])
    transformed = apply_digital_zoom(image, 2.0)
    assert transformed.shape == image.shape
    assert 23 <= int(transformed[30, 0, 0]) <= 26
    assert 73 <= int(transformed[30, -1, 0]) <= 76
