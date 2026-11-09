"""Deriving the calibration table from a config plus the stored artifacts."""

from pathlib import Path

import pytest

from vision_system.apps.calibration_gui.status import (
    MIN_REFERENCES_FOR_EXTRINSICS,
    CalibrationOverview,
    describe_calibrations,
)
from vision_system.calibration.board import BoardSpec, board_checksum
from vision_system.core.config import (
    AppConfig,
    ArucoConfig,
    CameraCalibration,
    CameraConfig,
    ReferenceMarkerConfig,
)

CALIBRATIONS = Path("calibrations")


def _config(*cameras: CameraConfig, references: int = 0) -> AppConfig:
    markers = [
        ReferenceMarkerConfig(
            id=index,
            size_m=0.1,
            position_m=(float(index), 0.0, 0.0),
            orientation_xyzw=(0.0, 0.0, 0.0, 1.0),
        )
        for index in range(references)
    ]
    return AppConfig(
        cameras=list(cameras),
        aruco=ArucoConfig(reference_markers=markers, reference_ids=[m.id for m in markers]),
    )


def _calibration(camera_id: str, **overrides) -> CameraCalibration:
    payload = dict(
        camera_id=camera_id,
        source=0,
        image_size=(1920, 1080),
        camera_matrix=[[600.0, 0.0, 960.0], [0.0, 600.0, 540.0], [0.0, 0.0, 1.0]],
        distortion=[0.0] * 14,
        intrinsic_median_error_px=0.31,
        intrinsic_p95_error_px=0.52,
        captured_at="2026-09-01T10:00:00+00:00",
        opencv_version="4.14.0",
        board_checksum=board_checksum(BoardSpec()),
    )
    payload.update(overrides)
    return CameraCalibration(**payload)


def _overview(config, calibrations, **kwargs):
    return describe_calibrations(
        config, calibrations, calibrations_dir=CALIBRATIONS, **kwargs
    )


def test_a_camera_without_an_artifact_is_reported_as_missing():
    config = _config(CameraConfig(id="cam_0", source=0), CameraConfig(id="cam_1", source=1))
    overview = _overview(config, {"cam_0": _calibration("cam_0")})
    missing = overview.status("cam_1")
    assert missing.has_intrinsics is False
    assert missing.intrinsic_median_error_px is None
    assert missing.artifact_path is None
    assert overview.missing("intrinsics") == ("cam_1",)


@pytest.mark.parametrize(
    "cameras",
    [
        ("cam_0", "cam_1"),
        ("cam_0", "cam_1", "cam_2"),
        ("cam_0", "cam_1", "cam_2", "cam_3"),
    ],
)
def test_the_table_follows_the_configured_roster(cameras):
    # The arena runs with two, three or four cameras; nothing may assume cam_0..cam_3.
    config = _config(*(CameraConfig(id=name, source=i) for i, name in enumerate(cameras)))
    assert _overview(config, {}).camera_ids() == cameras


def test_a_calibration_taken_at_another_digital_zoom_is_stale():
    # Changing the crop changes the intrinsics; the CLI refuses such an artifact and
    # the panel must say the same thing rather than invent a second rule.
    config = _config(CameraConfig(id="cam_0", source=0, digital_zoom=1.75))
    status = _overview(config, {"cam_0": _calibration("cam_0")}).status("cam_0")
    assert status.geometry_error == "digital zoom differs"
    assert status.stale is True
    assert status.complete("intrinsics") is False


def test_a_calibration_taken_at_another_resolution_is_stale():
    config = _config(CameraConfig(id="cam_0", source=0, width=1280, height=720))
    status = _overview(config, {"cam_0": _calibration("cam_0")}).status("cam_0")
    assert status.geometry_error == "image size differs"


def test_a_board_from_another_format_is_flagged():
    config = _config(CameraConfig(id="cam_0", source=0))
    a3 = _calibration("cam_0", board_checksum=board_checksum(BoardSpec.for_format("a3")))
    status = _overview(config, {"cam_0": a3}).status("cam_0")
    assert status.board_matches is False
    assert status.stale is True


def test_the_board_check_uses_the_selected_format():
    config = _config(CameraConfig(id="cam_0", source=0))
    a3_spec = BoardSpec.for_format("a3")
    a3 = _calibration("cam_0", board_checksum=board_checksum(a3_spec))
    status = _overview(config, {"cam_0": a3}, board_spec=a3_spec).status("cam_0")
    assert status.board_matches is True
    assert status.stale is False


def test_poor_intrinsics_are_reported_but_not_stale():
    config = _config(CameraConfig(id="cam_0", source=0))
    poor = _calibration("cam_0", intrinsic_median_error_px=2.4, intrinsic_p95_error_px=4.0)
    status = _overview(config, {"cam_0": poor}).status("cam_0")
    assert status.intrinsic_quality_passed is False
    assert status.stale is False, "a bad fit is not the same thing as an invalid one"
    assert status.complete("intrinsics") is False


def test_extrinsics_are_only_complete_once_a_world_pose_exists():
    config = _config(CameraConfig(id="cam_0", source=0))
    intrinsics_only = _overview(config, {"cam_0": _calibration("cam_0")}).status("cam_0")
    assert intrinsics_only.complete("intrinsics") is True
    assert intrinsics_only.has_extrinsics is False
    assert intrinsics_only.complete("extrinsics") is False

    placed = _calibration(
        "cam_0",
        world_from_camera=[[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, 1.0, 2.0], [0, 0, 0, 1.0]],
        extrinsic_quality_passed=True,
    )
    assert _overview(config, {"cam_0": placed}).status("cam_0").complete("extrinsics") is True


def test_a_moved_source_is_noticed_without_invalidating_the_artifact():
    config = _config(CameraConfig(id="cam_0", source=3))
    status = _overview(config, {"cam_0": _calibration("cam_0", source=0)}).status("cam_0")
    assert status.source_matches is False
    assert status.stale is False


def test_reference_markers_gate_the_extrinsics_step():
    few = _config(CameraConfig(id="cam_0", source=0), references=2)
    enough = _config(CameraConfig(id="cam_0", source=0), references=MIN_REFERENCES_FOR_EXTRINSICS)
    assert _overview(few, {}).has_enough_references() is False
    assert _overview(enough, {}).has_enough_references() is True


def test_an_empty_roster_is_never_reported_as_complete():
    # AppConfig refuses an empty roster, but the overview is also built from a
    # partially loaded state, and "nothing to do" must not read as "all done".
    empty = CalibrationOverview(cameras=(), calibrations_dir=CALIBRATIONS)
    assert empty.complete("intrinsics") is False
    assert empty.missing("intrinsics") == ()
