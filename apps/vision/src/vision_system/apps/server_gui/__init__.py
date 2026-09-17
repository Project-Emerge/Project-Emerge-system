"""Tkinter control panel that launches the fusion server and watches a distributed deployment.

The distributed runtime is a set of independent processes (one ``vision-node`` per
camera PC plus one ``vision-server``), so the usual debugging question is not "what
does the fusion compute" but "who is actually talking to whom". This panel starts and
stops the local ``vision-server`` process, streams its console output, and renders the
roster exactly as the two sides see it: what each node claims to publish and what the
coordinator claims to receive.

The package is layered so that everything except ``view`` is importable with no
display: ``supervisor`` spawns the child, ``controller`` holds the behaviour,
``presenters`` and ``strings`` do the wording, and ``view`` only paints.
"""

from __future__ import annotations

import argparse
import os
import signal
from collections.abc import Iterable, Sequence
from pathlib import Path

from ...gui.toolkit import GuiUnavailable, load_toolkit
from ...gui.world_view import VIEWPORT_QUANTUM_M, WorldCanvas, fit_viewport
from ...monitoring.deployment import PRESENCE_TIMEOUT_NS, CameraIssue, CameraRow, DeploymentStatus
from ...monitoring.listener import StatusMonitor
from ...monitoring.world import STALE_POSE_NS, WORLD_MARGIN_M, TrackedTag, WorldModel
from ...transport.diagnostics import configure_diagnostics
from . import strings
from .controller import ServerPanelController, options_from_fields, roster_from_config
from .models import LauncherFields, Notice, NoticeCode, PanelSnapshot
from .supervisor import (
    DEFAULT_CACHE,
    DEFAULT_CALIBRATIONS,
    MAX_LOG_LINES,
    STOP_TIMEOUT_S,
    ServerLaunchOptions,
    ServerProcess,
    build_server_command,
    build_server_environment,
)
from .view import ServerPanelWindow

__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_CALIBRATIONS",
    "MAX_LOG_LINES",
    "PRESENCE_TIMEOUT_NS",
    "STALE_POSE_NS",
    "STOP_TIMEOUT_S",
    "VIEWPORT_QUANTUM_M",
    "WORLD_MARGIN_M",
    "CameraIssue",
    "CameraRow",
    "DeploymentStatus",
    "LauncherFields",
    "Notice",
    "NoticeCode",
    "PanelSnapshot",
    "ServerLaunchOptions",
    "ServerPanelController",
    "ServerPanelWindow",
    "ServerProcess",
    "StatusMonitor",
    "TrackedTag",
    "WorldCanvas",
    "WorldModel",
    "build_server_command",
    "build_server_environment",
    "fit_viewport",
    "options_from_fields",
    "roster_from_config",
    "build_parser",
    "parse_args",
    "server_gui_main",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=strings.CLI_DESCRIPTION)
    parser.add_argument("--config", type=Path, help=strings.CLI_CONFIG_HELP)
    parser.add_argument("--calibrations", type=Path, default=DEFAULT_CALIBRATIONS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--mqtt-host",
        default=os.getenv("VISION_MQTT_HOST", "localhost"),
        help=strings.CLI_MQTT_HOST_HELP,
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=int(os.getenv("VISION_MQTT_PORT", "1883"))
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-debug-window", action="store_true", help=strings.CLI_NO_DEBUG_HELP
    )
    parser.add_argument("--no-mqtt", action="store_true", help=strings.CLI_NO_MQTT_HELP)
    return parser


def parse_args(argv: Sequence[str] | None = None) -> tuple[ServerLaunchOptions, bool]:
    """argv -> (options, verbose). Pure, so the flag wiring is testable headless."""
    args = build_parser().parse_args(argv)
    options = ServerLaunchOptions(
        config=args.config,
        calibrations=args.calibrations,
        cache=args.cache,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        debug=not args.no_debug_window,
        no_mqtt=args.no_mqtt,
        verbose=args.verbose,
    )
    return options, args.verbose


def server_gui_main(argv: Sequence[str] | None = None) -> None:
    options, verbose = parse_args(argv)
    diagnostic_path = configure_diagnostics("vision-server-gui", verbose=verbose)
    print(strings.DIAGNOSTIC_LOG.format(path=diagnostic_path))
    cameras: Iterable[str] = ()
    try:
        _, cameras = roster_from_config(options.config, options.cache)
    except (OSError, ValueError) as error:
        print(strings.CONFIG_UNREADABLE.format(error=error))
    # A missing toolkit or display is an environment problem, not a typo in the
    # command line, so it exits with a plain message instead of an argparse usage.
    try:
        toolkit = load_toolkit()
    except GuiUnavailable as error:
        raise SystemExit(str(error)) from error
    controller = ServerPanelController(options, list(cameras))
    try:
        window = ServerPanelWindow(toolkit, controller)
    except toolkit.tk.TclError as error:
        controller.shutdown()
        raise SystemExit(strings.DISPLAY_UNAVAILABLE.format(error=error)) from error
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda signum, frame: window.request_stop())
    window.run()


if __name__ == "__main__":
    server_gui_main()
