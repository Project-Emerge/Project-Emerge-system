import cv2
import numpy as np

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


def test_all_camera_zoom_settings_are_required() -> None:
    try:
        build_camera_settings_config(AppConfig(), {"cam_0": 100})
    except ValueError as error:
        assert "all four" in str(error)
    else:
        raise AssertionError("missing camera settings should be rejected")


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
