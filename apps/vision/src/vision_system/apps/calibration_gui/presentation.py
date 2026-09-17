"""Every user-facing string of the calibration panel, in English and in one place.

Two kinds of wording live here. The panel's own chrome, and the translation of the
codes the domain emits — folder rejection reasons, preview failures, coverage gaps.
The console pane is the exception on purpose: it shows the raw output of the
command that was run, which is the tool's own voice and stays as it is.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from ...calibration.samples import CoverageGap
from .controller import Notice, NoticeCode
from .status import CalibrationOverview, CameraStatus

# --------------------------------------------------------------------- window
WINDOW_TITLE: Final = "VisionSystem · calibration"
HEADER_CONFIG: Final = "Config"
BROWSE_BUTTON: Final = "Browse…"
REFRESH_BUTTON: Final = "Refresh"
CANCEL_BUTTON: Final = "Cancel"
ALL_FILES: Final = "All files"
CONSOLE_TAB: Final = "Console"
CLEAR_BUTTON: Final = "Clear"
NO_CONFIG_SELECTED: Final = "no configuration selected"
STEP_PREFIX: Final = "{index} {title}"

# --------------------------------------------------------------------- tables
UNKNOWN: Final = "—"
ROSTER_COLUMNS: Final = (
    ("camera", "Camera", 100),
    ("source", "Source", 110),
    ("status", "Status", 130),
    ("note", "Note", 320),
)
INTRINSICS_COLUMNS: Final = (
    ("camera", "Camera", 100),
    ("status", "Status", 130),
    ("median", "Median err.", 110),
    ("p95", "p95 err.", 100),
    ("board", "Board", 90),
    ("captured", "Captured", 180),
)
EXTRINSICS_COLUMNS: Final = (
    ("camera", "Camera", 100),
    ("status", "Status", 130),
    ("median", "Median err.", 110),
    ("p95", "p95 err.", 100),
    ("note", "Note", 260),
)

STATUS_OK: Final = "ok"
STATUS_MISSING: Final = "missing"
STATUS_STALE: Final = "stale"
STATUS_LOW_QUALITY: Final = "low quality"
STATUS_NOT_PLACED: Final = "not placed"
PIXELS: Final = "{value:.2f} px"
BOARD_MATCHES: Final = "matches"
BOARD_DIFFERS: Final = "differs ({format})"

# ------------------------------------------------------- domain code wording
REJECTION_REASONS: Final[dict[str, str]] = {
    "accepted": "accepted",
    "unreadable_image": "the file could not be read as an image",
    "resolution_mismatch": "the photo resolution differs from the camera configuration",
    "not_enough_charuco_corners": "too few ChArUco corners were detected",
    "board_pose_unavailable": "the board pose could not be estimated",
    "board_too_close_to_image_border": "the board sits too close to the image border",
    "blurred_image": "the photo is too blurred",
    "duplicate_board_pose": "the board pose repeats one already captured",
}

PREVIEW_FAILURES: Final[dict[str, str]] = {
    "device_missing": "device missing",
    "permission_denied": "permission denied",
    "device_busy": "device busy",
    "not_video_capture_node": "metadata node, not a capture device",
    "frame_timeout": "no frame arrived",
    "v4l2_open_failed": "V4L2 open failed",
}

COVERAGE_GRID: Final = "Move the board: {detail}"
COVERAGE_SCALE: Final[dict[str, str]] = {
    "far": "Move the board further away",
    "medium": "Bring the board to a medium distance",
    "near": "Bring the board closer",
}
COVERAGE_TILT: Final[dict[str, str]] = {
    "left": "Tilt the left edge towards the camera",
    "right": "Tilt the right edge towards the camera",
    "up": "Tilt the top edge towards the camera",
    "down": "Tilt the bottom edge towards the camera",
}
COVERAGE_COMPLETE: Final = "Coverage complete"

# ------------------------------------------------------------------- notices
NOTICE_TEXT: Final[dict[NoticeCode, str]] = {
    NoticeCode.NO_CONFIG: "[gui] select a configuration file first",
    NoticeCode.CONFIG_UNREADABLE: "[gui] configuration unreadable: {error}",
    NoticeCode.REFRESHED: "[gui] calibration state reloaded (cameras: {cameras})",
    NoticeCode.STEP_BLOCKED: "[gui] {reason}",
    NoticeCode.ACTION_BLOCKED: "[gui] {reason}",
    NoticeCode.ALREADY_BUSY: "[gui] another operation is already running",
    NoticeCode.CAMERA_BUSY: "[gui] {camera} is already in use by another operation",
    NoticeCode.QUEUED: "[gui] queued: {titles}",
    NoticeCode.TASK_STARTED: "[gui] {title}…",
    NoticeCode.TASK_FINISHED: "[gui] done",
    NoticeCode.TASK_FAILED: "[gui] failed: {error}",
    NoticeCode.CANCELLED: "[gui] cancelled",
    NoticeCode.COMMAND_FINISHED: (
        "[gui] {title} finished (exit {code}) — see the table for what was written"
    ),
    NoticeCode.NOTHING_TO_RUN: "[gui] nothing to run: no camera is configured",
}


def describe_coverage_gap(gap: CoverageGap | None) -> str:
    """The same guidance the Italian wizard overlay gives, worded in English."""
    if gap is None:
        return COVERAGE_COMPLETE
    if gap.kind == "grid":
        return COVERAGE_GRID.format(detail=gap.detail)
    if gap.kind == "scale":
        return COVERAGE_SCALE[gap.detail]
    return COVERAGE_TILT[gap.detail]


def describe_rejection(reason: str) -> str:
    """Never render a raw identifier; an unknown code is still readable."""
    return REJECTION_REASONS.get(reason, reason.replace("_", " "))


def describe_preview_failure(reason: str) -> str:
    return PREVIEW_FAILURES.get(reason, reason.replace("_", " "))


def _pixels(value: float | None) -> str:
    return UNKNOWN if value is None else PIXELS.format(value=value)


def intrinsic_status(camera: CameraStatus) -> str:
    if not camera.has_intrinsics:
        return STATUS_MISSING
    if camera.stale:
        return STATUS_STALE
    if camera.intrinsic_quality_passed is False:
        return STATUS_LOW_QUALITY
    return STATUS_OK


def extrinsic_status(camera: CameraStatus) -> str:
    if not camera.has_intrinsics:
        return STATUS_MISSING
    if camera.stale:
        return STATUS_STALE
    if not camera.has_extrinsics:
        return STATUS_NOT_PLACED
    if camera.extrinsic_quality_passed is False:
        return STATUS_LOW_QUALITY
    return STATUS_OK


def camera_note(camera: CameraStatus, *, board_format: str) -> str:
    """The most actionable thing about this row, or nothing."""
    if camera.geometry_error is not None:
        return camera.geometry_error
    if camera.board_matches is False:
        return BOARD_DIFFERS.format(format=board_format)
    if not camera.source_matches:
        return f"configured source {camera.source} differs from the calibrated one"
    return ""


def roster_values(camera: CameraStatus, *, board_format: str) -> tuple[str, ...]:
    return (
        camera.camera_id,
        str(camera.source),
        intrinsic_status(camera),
        camera_note(camera, board_format=board_format),
    )


def intrinsics_values(camera: CameraStatus, *, board_format: str) -> tuple[str, ...]:
    if camera.board_matches is None:
        board = UNKNOWN
    else:
        board = BOARD_MATCHES if camera.board_matches else BOARD_DIFFERS.format(format=board_format)
    return (
        camera.camera_id,
        intrinsic_status(camera),
        _pixels(camera.intrinsic_median_error_px),
        _pixels(camera.intrinsic_p95_error_px),
        board,
        camera.captured_at or UNKNOWN,
    )


def extrinsics_values(camera: CameraStatus, *, board_format: str) -> tuple[str, ...]:
    return (
        camera.camera_id,
        extrinsic_status(camera),
        _pixels(camera.extrinsic_median_error_px),
        _pixels(camera.extrinsic_p95_error_px),
        camera_note(camera, board_format=board_format),
    )


TABLE_COLUMNS: Final = {
    "roster": ROSTER_COLUMNS,
    "intrinsics": INTRINSICS_COLUMNS,
    "extrinsics": EXTRINSICS_COLUMNS,
}
TABLE_ROWS: Final = {
    "roster": roster_values,
    "intrinsics": intrinsics_values,
    "extrinsics": extrinsics_values,
}


def table_rows(kind: str, overview: CalibrationOverview) -> list[tuple[str, ...]]:
    """Render the table a step asked for, over the configured roster."""
    render = TABLE_ROWS[kind]
    return [render(camera, board_format=overview.board_format) for camera in overview.cameras]


def format_notice(notice: Notice) -> str:
    """Word a notice code. Every code must have an entry, so a miss raises KeyError."""
    fields = dict(notice.fields)
    for key in ("cameras", "titles"):
        value = fields.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str):
            fields[key] = ", ".join(str(item) for item in value) or UNKNOWN
    return NOTICE_TEXT[notice.code].format(**fields)
