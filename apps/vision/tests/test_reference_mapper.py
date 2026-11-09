import cv2
import numpy as np
import pytest

from vision_system.core.aruco import aruco_dictionary
from vision_system.core.config import AppConfig, CameraConfig, ReferenceMarkerConfig
from vision_system.mapping.planar import (
    AUTO_CAPTURE_STABLE_FRAMES,
    LiveCameraPanel,
    LiveSceneCapture,
    config_with_anchor_frame,
    config_with_references,
    detect_marker_centers,
    detect_planar_references,
    image_to_world,
    planar_mapping,
    selected_capture_cameras,
)


def test_live_capture_camera_selection() -> None:
    config = AppConfig()
    assert [camera.id for camera in selected_capture_cameras(config, "all")] == [
        "cam_0",
        "cam_1",
        "cam_2",
        "cam_3",
    ]
    assert [camera.id for camera in selected_capture_cameras(config, "cam_2")] == ["cam_2"]
    with pytest.raises(ValueError, match="sconosciuta"):
        selected_capture_cameras(config, "cam_9")


def test_rectangle_mapping_converts_pixels_to_world_metres() -> None:
    pixels = np.array([[100, 500], [900, 500], [800, 100], [200, 100]], dtype=np.float64)
    mapping = planar_mapping(pixels, 8.0, 4.0, "rectangle")

    recovered = image_to_world(pixels, mapping)

    np.testing.assert_allclose(recovered, [[0, 0], [8, 0], [8, 4], [0, 4]], atol=1e-6)


def test_detect_planar_references_recovers_xy_and_yaw() -> None:
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 7, 200)
    image[200:400, 300:500] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
    controls = np.array([[0, 600], [800, 600], [800, 0], [0, 0]], dtype=np.float64)
    mapping = planar_mapping(controls, 8.0, 6.0, "rectangle")

    references, _, _ = detect_planar_references(
        image, mapping, "DICT_4X4_50", marker_size_m=0.2, plane_z_m=0.5
    )

    assert len(references) == 1
    assert references[0].id == 7
    np.testing.assert_allclose(references[0].position_m, [4, 3, 0.5], atol=0.02)
    np.testing.assert_allclose(references[0].orientation_xyzw, [0, 0, 0, 1], atol=0.01)


def test_config_with_references_preserves_config_and_increments_revision() -> None:
    config = AppConfig(revision=8, site="lab-test")
    references = [
        ReferenceMarkerConfig(
            id=4,
            size_m=0.15,
            position_m=(1, 2, 0),
            orientation_xyzw=(0, 0, 0, 1),
        )
    ]

    updated = config_with_references(config, references)

    assert updated.revision == 9
    assert updated.site == "lab-test"
    assert updated.aruco.reference_markers == references
    assert [camera.source for camera in updated.cameras] == [5, 1, 2, 4]


def test_config_with_anchor_frame_fixes_world_frame_on_anchors() -> None:
    references = [
        ReferenceMarkerConfig(
            id=marker_id,
            size_m=0.07,
            position_m=(9, 9, 9),
            orientation_xyzw=(0, 0, 0, 1),
        )
        for marker_id in (13, 15, 18, 19)
    ]
    config = config_with_references(AppConfig(revision=3), references)

    updated = config_with_anchor_frame(config, [13, 15, 19, 18], 0.6, 0.6, 0.0)

    frame = updated.aruco.anchor_frame
    assert frame is not None
    assert (frame.origin_id, frame.x_axis_id, frame.opposite_id, frame.y_axis_id) == (
        13,
        15,
        19,
        18,
    )
    positions = {marker.id: marker.position_m for marker in updated.aruco.reference_markers}
    assert positions == {13: (0, 0, 0), 15: (0.6, 0, 0), 19: (0.6, 0.6, 0), 18: (0, 0.6, 0)}


def test_config_with_anchor_frame_requires_four_markers() -> None:
    with pytest.raises(ValueError, match="quattro"):
        config_with_anchor_frame(AppConfig(), [13, 15, 19], 0.6, 0.6, 0.0)


def test_detect_marker_centers_returns_ids_and_centers() -> None:
    image = np.full((600, 800, 3), 255, dtype=np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_4X4_50"), 7, 200)
    image[200:400, 300:500] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)

    centers, ids, _, _ = detect_marker_centers(image, "DICT_4X4_50")

    assert ids == [7]
    assert centers[0][0] == pytest.approx(400, abs=2)
    assert centers[0][1] == pytest.approx(300, abs=2)


def _panel_with_ids(marker_ids: list[int]) -> LiveCameraPanel:
    return LiveCameraPanel(
        camera=CameraConfig(id="cam_0", source=0),
        capture=None,  # type: ignore[arg-type]
        frame=np.zeros((10, 10, 3), dtype=np.uint8),
        online=True,
        detected_ids=marker_ids,
    )


def test_auto_capture_requires_stable_visibility_of_all_anchors() -> None:
    capture = LiveSceneCapture([], "DICT_4X4_50", auto_anchor_ids=[13, 15, 18, 19])
    panel = _panel_with_ids([13, 15, 18, 19])
    capture.panels = [panel]
    for _ in range(AUTO_CAPTURE_STABLE_FRAMES - 1):
        assert capture._auto_capture_candidate() is None
    assert capture._auto_capture_candidate() is panel
    panel.detected_ids = [13, 15]
    assert capture._auto_capture_candidate() is None
    assert capture.auto_stable["cam_0"] == 0


def test_auto_capture_inactive_without_anchor_ids() -> None:
    capture = LiveSceneCapture([], "DICT_4X4_50")
    capture.panels = [_panel_with_ids([13, 15, 18, 19])]
    assert capture._auto_capture_candidate() is None


def test_auto_capture_min_markers_triggers_on_four_visible_markers() -> None:
    capture = LiveSceneCapture([], "DICT_4X4_50", auto_min_markers=4)
    panel = _panel_with_ids([10, 11, 12, 13])
    capture.panels = [panel]
    for _ in range(AUTO_CAPTURE_STABLE_FRAMES - 1):
        assert capture._auto_capture_candidate() is None
    assert capture._auto_capture_candidate() is panel
    panel.detected_ids = [10, 11, 12]
    assert capture._auto_capture_candidate() is None
