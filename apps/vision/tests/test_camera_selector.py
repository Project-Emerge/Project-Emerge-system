import cv2
import numpy as np
import pytest

from vision_system.apps import camera_selector
from vision_system.apps.camera_selector import (
    PREVIEW_PROFILES,
    build_camera_config,
    open_previews,
    open_previews_with_failures,
)
from vision_system.core.config import AppConfig, ArucoConfig, CameraConfig, MobileMarkerConfig


class FakeCapture:
    def __init__(self, frame=None, *, opened: bool = True) -> None:
        self.frame = frame
        self.opened = opened
        self.released = False
        self.calls: list[tuple[int, float]] = []
        self.properties: dict[int, float] = {}

    def isOpened(self) -> bool:
        return self.opened

    def set(self, property_id: int, value: float) -> bool:
        self.calls.append((property_id, value))
        self.properties[property_id] = value
        return True

    def get(self, property_id: int) -> float:
        return self.properties.get(property_id, 0.0)

    def read(self):
        if self.frame is None:
            self.opened = False
            return False, None
        return True, self.frame

    def getBackendName(self) -> str:
        return "FAKE"

    def release(self) -> None:
        self.opened = False
        self.released = True


def test_preview_negotiates_mjpeg_before_dimensions(monkeypatch) -> None:
    capture = FakeCapture(np.zeros((480, 640, 3), dtype=np.uint8))
    monkeypatch.setattr(camera_selector, "open_video_capture", lambda source: capture)

    previews = open_previews([2], timeout_s=0.01)

    assert len(previews) == 1
    assert previews[0].profile == "mjpeg"
    assert capture.calls[0][0] == cv2.CAP_PROP_FOURCC
    assert [property_id for property_id, _ in capture.calls[1:4]] == [
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
    ]
    previews[0].close()


def test_preview_retries_with_lower_fps_after_frame_failure(monkeypatch) -> None:
    captures = [
        FakeCapture(),
        FakeCapture(np.zeros((480, 640, 3), dtype=np.uint8)),
    ]
    monkeypatch.setattr(camera_selector, "open_video_capture", lambda source: captures.pop(0))
    monkeypatch.setattr(camera_selector.time, "sleep", lambda duration: None)

    previews = open_previews([2], timeout_s=0.01)

    assert len(previews) == 1
    assert previews[0].profile == "mjpeg-low-fps"
    assert previews[0].capture.properties[cv2.CAP_PROP_FPS] == PREVIEW_PROFILES[1].fps
    previews[0].close()


def test_preview_reports_v4l2_open_failure(monkeypatch) -> None:
    captures = [FakeCapture(opened=False) for _ in PREVIEW_PROFILES]
    monkeypatch.setattr(camera_selector, "open_video_capture", lambda source: captures.pop(0))
    monkeypatch.setattr(camera_selector.time, "sleep", lambda duration: None)
    monkeypatch.setattr(
        camera_selector,
        "inspect_video_node",
        lambda source: {
            "path": f"/dev/video{source}",
            "exists": True,
            "readable": True,
            "writable": True,
            "capture_capable": True,
        },
    )

    previews, failures = open_previews_with_failures([6], timeout_s=0.01)

    assert previews == []
    assert len(failures) == 1
    assert failures[0].source == 6
    assert failures[0].reason == "v4l2_open_failed"
    assert len(failures[0].attempts) == len(PREVIEW_PROFILES)


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


def test_subset_camera_selection_assigns_only_selected() -> None:
    base = AppConfig(revision=2)
    result = build_camera_config(base, {0: 5, 1: 1, 2: 2})
    assert len(result.cameras) == 3
    assert [camera.id for camera in result.cameras] == ["cam_0", "cam_1", "cam_2"]
    assert [camera.source for camera in result.cameras] == [5, 1, 2]
    assert result.revision == 3


def test_empty_camera_selection_raises_error() -> None:
    with pytest.raises(ValueError, match="at least one camera must be assigned"):
        build_camera_config(AppConfig(), {})


def test_invalid_camera_index_raises_error() -> None:
    with pytest.raises(ValueError, match="invalid camera index"):
        build_camera_config(AppConfig(), {99: 5})


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

    # Selecting 2 cameras from 3 base cameras
    result_subset = build_camera_config(base, {0: 8, 2: 4})
    assert len(result_subset.cameras) == 2
    assert [camera.id for camera in result_subset.cameras] == ["cam_0", "cam_2"]
    assert [camera.source for camera in result_subset.cameras] == [8, 4]
    assert result_subset.revision == 8


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
