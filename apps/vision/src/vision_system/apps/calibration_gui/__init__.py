"""Tkinter panel that walks the operator through calibrating the arena.

The workflow is nine console tools in a fixed order, each with its own flags and
its own window. This panel is the menu in front of them: it shows which cameras
are already calibrated and how well, greys out a step whose prerequisites are not
met, and launches each stage with the right arguments.

Live stages keep their OpenCV windows and run as child processes: HighGUI has hard
thread affinity and a wizard loop only ends on a key press, so a child is the only
place one can be cancelled without taking the panel down with it. The status table,
not a child's exit code, is what the panel treats as the truth.

Everything except ``view`` imports with no display.
"""

from __future__ import annotations

import argparse
import signal
from collections.abc import Sequence
from pathlib import Path

from ...core.setup import MAX_CAMERAS, load_setup, load_template, save_setup
from ...gui.toolkit import GuiUnavailable, load_toolkit
from ...transport.diagnostics import configure_diagnostics
from .controller import CalibrationController
from .settings import (
    DEFAULT_BOARD_OUTPUT,
    DEFAULT_CACHE,
    DEFAULT_CALIBRATIONS,
    DEFAULT_PHOTO_ROOT,
    DEFAULT_TEMPLATE,
    GuiSettings,
)
from .status import CalibrationOverview, CameraStatus, describe_calibrations
from .steps import CALIBRATION_STEPS, StepDefinition

__all__ = [
    "CALIBRATION_STEPS",
    "CalibrationController",
    "CalibrationOverview",
    "CameraStatus",
    "GuiSettings",
    "StepDefinition",
    "build_parser",
    "calibration_gui_main",
    "describe_calibrations",
    "force_roster",
    "parse_args",
]

DESCRIPTION = (
    "Graphical panel for the arena calibration workflow: camera selection, board, "
    "intrinsics, reference markers and extrinsics"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--config", type=Path, help="configuration the panel works on")
    parser.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_TEMPLATE,
        help="example configuration a new roster and every added camera are cut from",
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--calibrations", type=Path, default=DEFAULT_CALIBRATIONS)
    parser.add_argument("--board-format", choices=("a4", "a3"), default="a4")
    parser.add_argument("--board-output", type=Path, default=DEFAULT_BOARD_OUTPUT)
    parser.add_argument(
        "--photo-root",
        type=Path,
        default=DEFAULT_PHOTO_ROOT,
        help="folder of ChArUco photos, one sub-folder per camera",
    )
    parser.add_argument(
        "--reference-markers", type=Path, help="reference marker file used by the extrinsics"
    )
    parser.add_argument(
        "--cameras",
        nargs="+",
        metavar="CAM_ID",
        help=(
            "force the shared roster before the panel opens, rewriting the "
            f"configuration: their ids (--cameras cam_2 cam_3) or how many "
            f"(--cameras 4, up to {MAX_CAMERAS}). Without it the roster is "
            "whatever the configuration already says"
        ),
    )
    parser.add_argument(
        "--publish-mqtt",
        action="store_true",
        help="let each stage publish over MQTT; off by default, the panel only reads",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> GuiSettings:
    """argv -> settings. Pure, so the flag wiring is testable with no display."""
    args = build_parser().parse_args(argv)
    return GuiSettings(
        config_path=args.config,
        template_path=args.base,
        cache_path=args.cache,
        calibrations_dir=args.calibrations,
        board_format=args.board_format,
        board_output=args.board_output,
        photo_root=args.photo_root,
        reference_markers_path=args.reference_markers,
        roster=tuple(args.cameras or ()),
        mqtt_enabled=args.publish_mqtt,
        verbose=args.verbose,
    )


def force_roster(settings: GuiSettings) -> None:
    """Apply ``--cameras`` by resizing the roster on disk, before the panel opens.

    Everything the panel shows is derived from the configuration, so forcing the
    roster means writing it there: the same save step 1 performs, minus the
    clicking — cameras added included, which come from ``--base`` exactly as they
    would in the panel. Naming ids replaces the roster with exactly those; a
    count keeps the cameras already configured and fills up from ``cam_0``. This
    is a --force, not a merge, and the local selection is re-validated by
    ``save_setup``.
    """
    if not settings.roster:
        return
    if settings.config_path is None:
        raise SystemExit("--cameras needs --config: the roster lives in that file")
    try:
        config = save_setup(
            settings.config_path,
            load_setup(settings.config_path),
            list(settings.roster),
            template=load_template(settings.template_path, settings.config_path),
        )
    except ValueError as error:
        raise SystemExit(f"--cameras: {error}") from error
    print(f"Roster forced to: {', '.join(camera.id for camera in config.cameras)}")


def calibration_gui_main(argv: Sequence[str] | None = None) -> None:
    settings = parse_args(argv)
    force_roster(settings)
    diagnostic_path = configure_diagnostics("vision-calibrate-gui", verbose=settings.verbose)
    print(f"Diagnostic log: {diagnostic_path}")
    # A missing toolkit or display is an environment problem, not a typo in the
    # command line, so it exits with a plain message instead of an argparse usage.
    try:
        toolkit = load_toolkit()
    except GuiUnavailable as error:
        raise SystemExit(str(error)) from error
    from .view.shell import CalibrationWindow

    controller = CalibrationController(settings)
    try:
        window = CalibrationWindow(toolkit, controller)
    except toolkit.tk.TclError as error:
        controller.shutdown()
        raise SystemExit(
            f"cannot open the GUI window (is DISPLAY set?): {error}"
        ) from error
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda signum, frame: window.request_stop())
    window.run()


if __name__ == "__main__":
    calibration_gui_main()
