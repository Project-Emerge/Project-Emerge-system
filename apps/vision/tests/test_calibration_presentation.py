"""Wording for the calibration panel, including the domain codes it surfaces."""

from pathlib import Path

import pytest

from vision_system.apps.calibration_gui import presentation
from vision_system.apps.calibration_gui.controller import Notice, NoticeCode
from vision_system.apps.calibration_gui.status import CalibrationOverview, CameraStatus
from vision_system.calibration.samples import Coverage, CoverageGap


def _camera(**overrides) -> CameraStatus:
    payload = dict(camera_id="cam_0", source=0, has_intrinsics=True, intrinsic_quality_passed=True)
    payload.update(overrides)
    return CameraStatus(**payload)


def test_every_notice_code_has_wording():
    assert set(presentation.NOTICE_TEXT) == set(NoticeCode)


def test_every_rejection_reason_the_domain_emits_has_an_english_label():
    # Taken from collect_folder_samples; a new code must not reach the operator raw.
    emitted = {
        "accepted",
        "unreadable_image",
        "resolution_mismatch",
        "not_enough_charuco_corners",
        "board_pose_unavailable",
        "board_too_close_to_image_border",
        "blurred_image",
        "duplicate_board_pose",
    }
    assert emitted <= set(presentation.REJECTION_REASONS)


def test_every_preview_failure_reason_has_an_english_label():
    from vision_system.apps.camera_selector import FAILURE_MESSAGES

    assert set(FAILURE_MESSAGES) == set(presentation.PREVIEW_FAILURES)


def test_an_unknown_code_is_still_readable():
    assert presentation.describe_rejection("some_new_reason") == "some new reason"
    assert presentation.describe_preview_failure("weird_thing") == "weird thing"


def test_every_coverage_gap_is_worded_in_english():
    full = Coverage()
    full.grid[:] = 1
    full.scales = {"far": 99, "medium": 99, "near": 99}
    full.tilts = dict.fromkeys(full.tilts, 99)
    assert presentation.describe_coverage_gap(full.gap()) == presentation.COVERAGE_COMPLETE
    for kind, detail in (
        ("grid", "top left"),
        ("scale", "far"),
        ("scale", "medium"),
        ("scale", "near"),
        ("tilt", "left"),
        ("tilt", "right"),
        ("tilt", "up"),
        ("tilt", "down"),
    ):
        rendered = presentation.describe_coverage_gap(CoverageGap(kind, detail))
        assert rendered and not rendered.startswith("Sposta")


@pytest.mark.parametrize(
    ("camera", "expected"),
    [
        (_camera(), presentation.STATUS_OK),
        (_camera(has_intrinsics=False, intrinsic_quality_passed=None), presentation.STATUS_MISSING),
        (_camera(geometry_error="digital zoom differs"), presentation.STATUS_STALE),
        (_camera(intrinsic_quality_passed=False), presentation.STATUS_LOW_QUALITY),
    ],
)
def test_intrinsic_status_separates_missing_stale_and_poor(camera, expected):
    assert presentation.intrinsic_status(camera) == expected


def test_extrinsic_status_says_not_placed_rather_than_missing():
    # The lens model is there; what is absent is the world pose.
    assert presentation.extrinsic_status(_camera()) == presentation.STATUS_NOT_PLACED
    placed = _camera(has_extrinsics=True, extrinsic_quality_passed=True)
    assert presentation.extrinsic_status(placed) == presentation.STATUS_OK


def test_errors_are_rendered_with_units_and_absent_ones_are_not_zero():
    values = presentation.intrinsics_values(
        _camera(intrinsic_median_error_px=0.3117, intrinsic_p95_error_px=None),
        board_format="a4",
    )
    assert "0.31 px" in values
    assert presentation.UNKNOWN in values


def test_the_note_names_the_board_format_it_compared_against():
    # Otherwise switching format makes every row cry wolf with no explanation.
    note = presentation.camera_note(_camera(board_matches=False), board_format="a3")
    assert "a3" in note


def test_the_geometry_problem_wins_over_the_board_one():
    camera = _camera(geometry_error="image size differs", board_matches=False)
    assert presentation.camera_note(camera, board_format="a4") == "image size differs"


def test_a_table_renders_one_row_per_configured_camera():
    overview = CalibrationOverview(
        cameras=(_camera(camera_id="cam_0"), _camera(camera_id="cam_2")),
        calibrations_dir=Path("calibrations"),
    )
    for kind in ("roster", "intrinsics", "extrinsics"):
        rows = presentation.table_rows(kind, overview)
        assert [row[0] for row in rows] == ["cam_0", "cam_2"]
        assert all(len(row) == len(presentation.TABLE_COLUMNS[kind]) for row in rows)


def test_notice_wording_lists_the_cameras_it_is_about():
    notice = Notice(
        NoticeCode.REFRESHED, {"cameras": ["cam_0", "cam_1"], "local": ["cam_1"]}
    )
    rendered = presentation.format_notice(notice)
    assert "cam_0, cam_1" in rendered
    # The local share is named separately: on a client PC the two lists differ,
    # and that difference is the thing the operator has to get right.
    assert "this PC: cam_1" in rendered


def test_a_finished_command_points_at_the_table_not_the_exit_code():
    notice = Notice(NoticeCode.COMMAND_FINISHED, {"title": "intrinsics cam_0", "code": 1})
    rendered = presentation.format_notice(notice)
    assert "table" in rendered
