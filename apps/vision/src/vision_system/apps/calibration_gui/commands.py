"""Assembling the vision-* command lines each stage runs as a child process.

Pure string building, kept apart from anything that spawns, because this is where
a launcher quietly gets a flag wrong and nothing notices until a wizard opens
against the wrong camera.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Sequence

from ...gui.process import CommandSpec
from .settings import GuiSettings


def console_script(name: str, module: str) -> list[str]:
    """The installed console script, or ``python -m`` when it is not on PATH."""
    binary = shutil.which(name)
    return [binary] if binary else [sys.executable, "-m", module]


def _calibrate(settings: GuiSettings) -> list[str]:
    """The `vision-calibrate` prefix, including its global options."""
    command = console_script("vision-calibrate", "vision_system.apps.calibrate_cli")
    if settings.config_path is not None:
        command += ["--config", str(settings.config_path)]
    command += ["--cache", str(settings.cache_path)]
    command += ["--calibrations", str(settings.calibrations_dir)]
    if settings.verbose:
        command.append("--verbose")
    if not settings.mqtt_enabled:
        command.append("--no-mqtt")
    return command


def build_board_command(settings: GuiSettings) -> CommandSpec:
    command = _calibrate(settings) + [
        "board",
        "--format",
        settings.board_format,
        "--output",
        str(settings.board_output),
    ]
    return CommandSpec(argv=tuple(command), title="board")


def build_probe_command(settings: GuiSettings) -> CommandSpec:
    return CommandSpec(argv=tuple(_calibrate(settings) + ["probe"]), title="probe")


def build_intrinsics_command(settings: GuiSettings, camera_id: str) -> CommandSpec:
    command = _calibrate(settings) + [
        "intrinsics",
        "--camera",
        camera_id,
        "--board-format",
        settings.board_format,
    ]
    return CommandSpec(
        argv=tuple(command), title=f"intrinsics {camera_id}", camera_id=camera_id
    )


def build_extrinsics_command(settings: GuiSettings, camera_id: str) -> CommandSpec:
    command = _calibrate(settings) + ["extrinsics", "--camera", camera_id]
    if settings.reference_markers_path is not None:
        command += ["--reference-markers", str(settings.reference_markers_path)]
    if settings.allow_low_quality:
        command.append("--allow-low-quality")
    return CommandSpec(
        argv=tuple(command), title=f"extrinsics {camera_id}", camera_id=camera_id
    )


def build_select_cameras_command(
    settings: GuiSettings,
    *,
    camera_ids: Sequence[str] = (),
    sources: Sequence[int] = (),
    force: bool = True,
) -> CommandSpec:
    command = console_script("vision-select-cameras", "vision_system.apps.camera_selector")
    if settings.config_path is not None:
        command += ["--base", str(settings.config_path), "--output", str(settings.config_path)]
    if camera_ids:
        command += ["--cameras", *camera_ids]
    if sources:
        command += ["--sources", *(str(source) for source in sources)]
    if force:
        command.append("--force")
    return CommandSpec(argv=tuple(command), title="select cameras", camera_id="*")


def build_configure_cameras_command(settings: GuiSettings) -> CommandSpec:
    command = console_script(
        "vision-configure-cameras", "vision_system.apps.camera_configurator"
    )
    command += ["--config", str(settings.config_path), "--force"]
    return CommandSpec(argv=tuple(command), title="configure cameras", camera_id="*")


REFERENCE_MAP_MODES = ("rectangle", "axes", "anchors")


def build_reference_map_command(
    settings: GuiSettings, *, mode: str = "rectangle", camera: str = "all"
) -> CommandSpec:
    if mode not in REFERENCE_MAP_MODES:
        raise ValueError(f"unsupported reference map mode: {mode}")
    command = console_script("vision-reference-map", "vision_system.apps.reference_mapper")
    command += ["--config", str(settings.config_path), "--mode", mode, "--camera", camera]
    return CommandSpec(argv=tuple(command), title=f"reference map ({mode})", camera_id="*")


def build_reference_stitch_command(settings: GuiSettings) -> CommandSpec:
    command = console_script(
        "vision-reference-stitch", "vision_system.apps.reference_stitcher"
    )
    command += ["--config", str(settings.config_path), "--camera", "all"]
    return CommandSpec(argv=tuple(command), title="reference stitch", camera_id="*")


def build_origin_command(settings: GuiSettings, *, camera: str = "all") -> CommandSpec:
    command = console_script("vision-select-origin", "vision_system.apps.origin_selector")
    command += ["--config", str(settings.config_path), "--camera", camera, "--force"]
    return CommandSpec(argv=tuple(command), title="select origin", camera_id="*")


def build_localizer_command(settings: GuiSettings, *, debug: bool = True) -> CommandSpec:
    command = console_script("vision-localizer", "vision_system.apps.calibrate_cli")
    if settings.config_path is not None:
        command += ["--config", str(settings.config_path)]
    command += ["--calibrations", str(settings.calibrations_dir)]
    if debug:
        command.append("--debug")
    if not settings.mqtt_enabled:
        command.append("--no-mqtt")
    return CommandSpec(argv=tuple(command), title="localizer", camera_id="*")


def build_server_gui_command(settings: GuiSettings) -> CommandSpec:
    command = console_script("vision-server-gui", "vision_system.apps.server_gui")
    if settings.config_path is not None:
        command += ["--config", str(settings.config_path)]
    command += ["--calibrations", str(settings.calibrations_dir)]
    return CommandSpec(argv=tuple(command), title="server panel")
