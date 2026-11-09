import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vision_system.core.config import (
    AnchorFrameConfig,
    AppConfig,
    ArucoConfig,
    CameraConfig,
    MobileMarkerConfig,
    ReferenceMarkerConfig,
    load_reference_markers,
)


def test_default_camera_sources_are_in_requested_order() -> None:
    config = AppConfig()
    assert [camera.source for camera in config.cameras] == [5, 1, 2, 4]
    assert config.base_topic == "vision/default/indoor-01"
    assert all(camera.zoom is None for camera in config.cameras)
    assert all(camera.digital_zoom == 1.0 for camera in config.cameras)
    assert config.aruco.auto_mobile_markers.enabled is False


def test_camera_zoom_must_be_non_negative() -> None:
    assert CameraConfig(id="test", source=0, zoom=125).zoom == 125
    with pytest.raises(ValidationError):
        CameraConfig(id="test", source=0, zoom=-1)


def test_digital_zoom_is_bounded() -> None:
    assert CameraConfig(id="test", source=0, digital_zoom=1.2).digital_zoom == 1.2
    with pytest.raises(ValidationError):
        CameraConfig(id="test", source=0, digital_zoom=0.99)


def test_at_least_one_camera_is_required() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        AppConfig(cameras=[])


def test_detector_tuning_parameters_are_bounded() -> None:
    aruco = ArucoConfig(perspective_remove_pixel_per_cell=2, error_correction_rate=0.8)
    assert aruco.perspective_remove_pixel_per_cell == 2
    assert aruco.error_correction_rate == 0.8
    with pytest.raises(ValidationError):
        ArucoConfig(perspective_remove_pixel_per_cell=0)
    with pytest.raises(ValidationError):
        ArucoConfig(error_correction_rate=1.1)


def test_three_cameras_are_supported() -> None:
    config = AppConfig(
        cameras=[
            CameraConfig(id="cam_0", source=5),
            CameraConfig(id="cam_1", source=1),
            CameraConfig(id="cam_2", source=2),
        ]
    )
    assert [camera.id for camera in config.cameras] == ["cam_0", "cam_1", "cam_2"]
    with pytest.raises(ValidationError, match="unique"):
        AppConfig(
            cameras=[
                CameraConfig(id="cam_0", source=5),
                CameraConfig(id="cam_1", source=1),
                CameraConfig(id="cam_2", source=2),
                CameraConfig(id="cam_0", source=4),
            ]
        )


def test_marker_id_cannot_have_two_roles() -> None:
    with pytest.raises(ValidationError, match="both mobile and reference"):
        AppConfig(
            aruco={
                "mobile_markers": [MobileMarkerConfig(id=7, size_m=0.12)],
                "reference_markers": [
                    ReferenceMarkerConfig(
                        id=7,
                        size_m=0.15,
                        position_m=(0, 0, 1),
                        orientation_xyzw=(0, 0, 0, 1),
                    )
                ],
            }
        )


def test_reference_id_shorthand_is_accepted_before_mapping() -> None:
    config = AppConfig(
        aruco={
            "reference_markers": [13, 15, 19, 21],
            "mobile_markers": [{"id": 23, "size_m": 0.12}],
        }
    )

    assert config.aruco.reference_ids == [13, 15, 19, 21]
    assert config.aruco.reference_markers == []


def test_declared_reference_ids_must_be_unique_and_not_mobile() -> None:
    with pytest.raises(ValidationError, match="duplicate reference id"):
        ArucoConfig(reference_ids=[13, 13])
    with pytest.raises(ValidationError, match="both mobile and reference"):
        ArucoConfig(
            reference_ids=[23],
            mobile_markers=[MobileMarkerConfig(id=23, size_m=0.12)],
        )


def test_reference_quaternion_is_normalized() -> None:
    marker = ReferenceMarkerConfig(
        id=1,
        size_m=0.15,
        position_m=(0, 0, 1),
        orientation_xyzw=(0, 0, 0, 2),
    )
    assert marker.orientation_xyzw == (0, 0, 0, 1)


def test_anchor_frame_assigns_exact_reference_positions() -> None:
    references = [
        ReferenceMarkerConfig(
            id=marker_id,
            size_m=0.07,
            position_m=(9, 9, 9),
            orientation_xyzw=(0, 0, 0, 1),
        )
        for marker_id in (13, 15, 18, 19)
    ]
    aruco = ArucoConfig(
        reference_markers=references,
        anchor_frame=AnchorFrameConfig(
            origin_id=18,
            x_axis_id=13,
            y_axis_id=15,
            opposite_id=19,
            x_distance_m=0.6,
            y_distance_m=0.5,
        ),
    )
    positions = {marker.id: marker.position_m for marker in aruco.reference_markers}
    assert positions == {
        18: (0, 0, 0),
        13: (0.6, 0, 0),
        15: (0, 0.5, 0),
        19: (0.6, 0.5, 0),
    }


def test_anchor_frame_requires_configured_reference_ids() -> None:
    with pytest.raises(ValidationError, match="not references"):
        ArucoConfig(
            anchor_frame=AnchorFrameConfig(
                origin_id=18,
                x_axis_id=13,
                y_axis_id=15,
                x_distance_m=0.6,
                y_distance_m=0.6,
            )
        )


@pytest.mark.parametrize(
    "wrap",
    [
        lambda markers: markers,
        lambda markers: {"reference_markers": markers},
        lambda markers: {"aruco": {"reference_markers": markers}},
    ],
)
def test_load_reference_markers_accepts_supported_documents(tmp_path: Path, wrap) -> None:
    markers = [
        {
            "id": 10,
            "size_m": 0.15,
            "position_m": [1, 2, 3],
            "orientation_xyzw": [0, 0, 0, 2],
        }
    ]
    path = tmp_path / "references.json"
    path.write_text(json.dumps(wrap(markers)), encoding="utf-8")

    loaded = load_reference_markers(path)

    assert loaded[0].id == 10
    assert loaded[0].orientation_xyzw == (0, 0, 0, 1)
