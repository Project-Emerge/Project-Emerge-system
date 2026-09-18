"""In-process stages: board generation, source probing, folder calibration.

These stay in the panel's process because they return rich results — per-image
rejection reasons, per-camera reports — that a subprocess would flatten back into
printed text. Each one reports progress and checks for cancellation between
cameras, which is the only granularity that matters here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ...calibration.board import BoardSpec, generate_board
from ...calibration.folder import FolderCalibrationResult, calibrate_camera_folder
from ...core.config import load_config
from ...core.setup import (
    launch_commands,
    load_template,
    save_setup,
    select_cameras,
    setup_path,
)
from ...gui.tasks import TaskSpec
from ...pipeline.capture import probe_camera
from .settings import resolve_camera_ids

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to a type checker
    from .steps import ActionContext


@dataclass(frozen=True)
class BoardResult:
    pdf: Path
    png: Path
    metadata: Path


def board_job(context: ActionContext) -> TaskSpec:
    """Render the printable ChArUco board for the selected format."""
    settings = context.settings

    def run(token, emit):
        spec = BoardSpec.for_format(settings.board_format)
        emit(f"generating the {spec.page_format.upper()} board…")
        pdf, png, metadata = generate_board(settings.board_output, spec)
        emit(f"written {pdf.name}, {png.name} and {metadata.name}")
        return BoardResult(pdf=pdf, png=png, metadata=metadata)

    return TaskSpec(id="board.generate", title="Generating the board", run=run)


def probe_job(context: ActionContext) -> TaskSpec:
    """Check every configured source answers at the resolution it was asked for."""
    settings = context.settings

    def run(token, emit):
        if settings.config_path is None:
            raise ValueError("select a configuration file first")
        config = load_config(settings.config_path)
        reports = []
        # Probing a camera plugged into another PC would just report a device
        # that is not here, so the default is this machine's share — the step's
        # own field is what widens it.
        targets = resolve_camera_ids(
            settings.cameras,
            [camera.id for camera in config.cameras],
            settings.local_camera_ids(config),
        )
        for camera in select_cameras(config, targets):
            token.raise_if_cancelled()
            emit(f"probing {camera.id} (source {camera.source})…")
            report = probe_camera(camera)
            reports.append(report)
            emit(
                f"{camera.id}: {'ok' if report.get('ok') else 'FAILED'} "
                f"{report.get('resolution_ok')} {report.get('effective_fps')} fps"
            )
        return reports

    return TaskSpec(id="cameras.probe", title="Probing the sources", run=run)


def folder_calibration_job(context: ActionContext) -> TaskSpec:
    """Calibrate intrinsics from folders of photos, one camera at a time.

    Looping here rather than calling calibrate_folder_tree is what buys per-camera
    progress and a cancellation point, without touching the calibration code.
    """
    settings = context.settings

    def run(token, emit):
        if settings.config_path is None:
            raise ValueError("select a configuration file first")
        config = load_config(settings.config_path)
        spec = BoardSpec.for_format(settings.board_format)
        root = settings.photo_root
        results: list[FolderCalibrationResult] = []
        for camera in select_cameras(config, settings.local_camera_ids(config)):
            token.raise_if_cancelled()
            folder = root / camera.id if (root / camera.id).is_dir() else root
            if not folder.is_dir():
                emit(f"{camera.id}: no photo folder at {folder}")
                continue
            emit(f"{camera.id}: reading {folder}…")
            result = calibrate_camera_folder(
                camera,
                folder,
                settings.calibrations_dir,
                spec,
                allow_low_quality=settings.allow_low_quality,
                sharpness_threshold=settings.sharpness_threshold,
            )
            results.append(result)
            emit(
                f"{camera.id}: {'written' if result.written else 'not written'} "
                f"(quality {'passed' if result.quality_passed else 'failed'})"
            )
        if not results:
            raise ValueError(f"no camera photo folders found under {root}")
        return results

    return TaskSpec(id="intrinsics.from_folder", title="Calibrating from photos", run=run)


@dataclass(frozen=True)
class SetupResult:
    """What saving the deployment settings changed."""

    config_path: Path
    camera_ids: tuple[str, ...]
    local_camera_ids: tuple[str, ...]
    commands: tuple[str, ...]


def save_setup_job(context: ActionContext) -> TaskSpec:
    """Set the shared roster and record this PC's share of the deployment."""
    settings = context.settings

    def run(token, emit):
        if settings.config_path is None:
            raise ValueError("select a configuration file first")
        template = load_template(settings.template_path, settings.config_path)
        emit(
            f"base: {settings.template_path}"
            if template is not None
            else f"base: {settings.template_path} not found, using the built-in defaults"
        )
        config = save_setup(
            settings.config_path, settings.setup, list(settings.roster), template
        )
        roster = tuple(camera.id for camera in config.cameras)
        local = tuple(settings.setup.local_ids(config))
        emit(f"deployment roster ({len(roster)}): {', '.join(roster)}")
        emit(
            f"attached to this PC: {', '.join(local) or 'none'}"
            f" · mode {settings.setup.mode}"
            f" · broker {settings.setup.mqtt_host}:{settings.setup.mqtt_port}"
        )
        emit(f"written {settings.config_path} and {setup_path(settings.config_path)}")
        # Echo the roster back as ids: a "3" typed to mean "three cameras" has
        # now become three names, and that is what the field should show.
        settings.roster = roster
        commands = tuple(launch_commands(settings.config_path, config, settings.setup))
        for line in commands:
            emit(line)
        return SetupResult(
            config_path=settings.config_path,
            camera_ids=roster,
            local_camera_ids=local,
            commands=commands,
        )

    return TaskSpec(id="deployment.save", title="Saving the deployment setup", run=run)


def launch_commands_job(context: ActionContext) -> TaskSpec:
    """Print the Make commands for the saved deployment, without writing anything."""
    settings = context.settings

    def run(token, emit):
        if settings.config_path is None:
            raise ValueError("select a configuration file first")
        config = load_config(settings.config_path)
        commands = launch_commands(settings.config_path, config, settings.setup)
        for line in commands:
            emit(line)
        return tuple(commands)

    return TaskSpec(id="deployment.commands", title="Listing the launch commands", run=run)
