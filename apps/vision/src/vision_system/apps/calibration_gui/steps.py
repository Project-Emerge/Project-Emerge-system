"""The calibration workflow as data: seven steps, their gates and their actions.

Navigation is a registry rather than a pile of callbacks, so adding a stage is
adding a tuple entry and the panel renders it without new view code. The
preconditions deliberately restate the gates ``vision-calibrate`` enforces, so a
step the panel greys out is exactly a step the CLI would refuse.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from ...gui.process import CommandSpec
from ...gui.tasks import TaskSpec
from . import commands, jobs
from .settings import GuiSettings, resolve_camera_ids
from .status import CalibrationOverview

TableKind = Literal["none", "roster", "intrinsics", "extrinsics"]
ActionKind = Literal["process", "task"]


@dataclass(frozen=True)
class StepReadiness:
    """Whether a step can be run, and if not, what to do about it."""

    ready: bool
    reason: str = ""


@dataclass(frozen=True)
class FieldDefinition:
    """One editable setting shown with a step."""

    id: str
    label: str
    kind: Literal["text", "path", "dir", "choice", "float", "bool"]
    choices: tuple[str, ...] = ()
    help: str = ""


@dataclass(frozen=True)
class ActionContext:
    """Everything an action needs to build its command or task."""

    settings: GuiSettings
    overview: CalibrationOverview
    camera_id: str | None = None


@dataclass(frozen=True)
class ActionDefinition:
    """A button. ``build`` turns the context into something runnable."""

    id: str
    label: str
    kind: ActionKind
    build: Callable[[ActionContext], CommandSpec | TaskSpec]
    # Expanded over the whole roster when the operator does not pick a camera.
    per_camera: bool = False
    enabled: Callable[[CalibrationOverview], bool] = lambda overview: True
    disabled_hint: str = ""


@dataclass(frozen=True)
class StepDefinition:
    """One entry of the left-hand menu."""

    id: str
    index: int
    title: str
    summary: str
    precondition: Callable[[CalibrationOverview], StepReadiness]
    actions: tuple[ActionDefinition, ...] = ()
    fields: tuple[FieldDefinition, ...] = ()
    table: TableKind = "none"


READY = StepReadiness(True)


def _needs_config_path(overview: CalibrationOverview) -> StepReadiness:
    """All step 1 needs is somewhere to write: the file itself may not exist yet."""
    if overview.config_path is None:
        return StepReadiness(False, "Type the configuration file to create in the header.")
    return READY


def _needs_config(overview: CalibrationOverview) -> StepReadiness:
    if overview.config_path is None:
        return StepReadiness(False, "Select a configuration file first.")
    # A valid configuration always holds at least one camera, so an empty roster
    # means the file could not be read — and sending the operator to the step
    # that writes it beats launching a tool that will fail on the same file.
    if not overview.cameras:
        return StepReadiness(
            False, "No configuration to work on yet; set the deployment up in step 1."
        )
    return READY


def _extrinsics_ready(whole: CalibrationOverview) -> StepReadiness:
    # The same gates calibrate_cli applies before running the wizard. The three
    # reasons are kept apart because they send the operator to different places:
    # a missing file, a camera whose geometry changed, and a fit that was too poor.
    # Applied to this PC's cameras only: the ones on another machine are that
    # machine's job, and waiting for them would leave a distributed deployment
    # with no PC on which this step is ever runnable.
    overview = whole.local()
    if not overview.cameras:
        return StepReadiness(False, "No camera is attached to this PC; see step 1.")
    absent = overview.without_intrinsics()
    if absent:
        return StepReadiness(False, f"Intrinsics are missing for: {', '.join(absent)}.")
    stale = overview.stale_cameras()
    if stale:
        return StepReadiness(
            False,
            f"The camera settings changed since calibration; re-run the intrinsics "
            f"for: {', '.join(stale)}.",
        )
    poor = overview.poor_intrinsics()
    if poor:
        return StepReadiness(
            False, f"The intrinsics did not pass the quality gates for: {', '.join(poor)}."
        )
    if not overview.has_enough_references():
        return StepReadiness(
            False,
            "At least 3 reference markers are required; map them in step 5.",
        )
    return READY


def _runtime_ready(overview: CalibrationOverview) -> StepReadiness:
    # The runtime is the one step that needs the *whole* roster: the fusion
    # server rebuilds every camera's observations from its extrinsics, so a
    # remote camera's artifact has to have been copied here first. As for
    # extrinsics, an artifact that went stale is not an absent one, and saying
    # "missing" would send the operator hunting for a file that exists.
    absent = overview.without_intrinsics()
    if absent:
        return StepReadiness(False, f"Intrinsics are missing for: {', '.join(absent)}.")
    stale = overview.stale_cameras()
    if stale:
        return StepReadiness(
            False,
            f"The camera settings changed since calibration; recalibrate: {', '.join(stale)}.",
        )
    unplaced = overview.without_extrinsics()
    if unplaced:
        return StepReadiness(False, f"Extrinsics are missing for: {', '.join(unplaced)}.")
    poor = tuple(
        c.camera_id for c in overview.cameras if c.extrinsic_quality_passed is False
    )
    if poor:
        return StepReadiness(
            False, f"The extrinsics did not pass the quality gates for: {', '.join(poor)}."
        )
    return READY


DEPLOYMENT_STEP = StepDefinition(
    id="deployment",
    index=1,
    title="Deployment",
    summary=(
        "How many webcams the arena has, and which of them are plugged into this PC. "
        "Every PC must save the same camera count; only the local selection differs."
    ),
    precondition=_needs_config_path,
    table="roster",
    fields=(
        FieldDefinition(
            "deployment_mode",
            "Deployment",
            "choice",
            choices=("single-pc", "distributed"),
            help="single-pc: every webcam is on this machine and one process fuses them.",
        ),
        FieldDefinition(
            "roster",
            "Cameras in the deployment",
            "text",
            help="Their ids, e.g. 'cam_2 cam_3' — or just how many, e.g. '4'.",
        ),
        FieldDefinition(
            "base_config",
            "Base configuration",
            "path",
            help=(
                "Template a new configuration and every added camera are cut from. "
                "Cameras already configured are never overwritten."
            ),
        ),
        FieldDefinition(
            "local_cameras",
            "Webcams attached to this PC",
            "text",
            help="Distributed mode only, e.g. 'cam_1 cam_2'. Empty means all of them.",
        ),
        FieldDefinition("mqtt_host", "MQTT broker", "text", help="Host or IP of the server PC."),
        FieldDefinition("mqtt_port", "Broker port", "text"),
    ),
    actions=(
        ActionDefinition(
            id="save",
            label="Save deployment",
            kind="task",
            build=lambda ctx: jobs.save_setup_job(ctx),
            enabled=lambda overview: overview.config_path is not None,
            disabled_hint="Select a configuration file first.",
        ),
        ActionDefinition(
            id="commands",
            label="Show launch commands",
            kind="task",
            build=lambda ctx: jobs.launch_commands_job(ctx),
            enabled=lambda overview: overview.config_path is not None,
            disabled_hint="Select a configuration file first.",
        ),
        # Reachable from here on purpose: when nothing is calibrated yet, the
        # roster panel is exactly what says why the cameras are not talking.
        ActionDefinition(
            id="server_panel",
            label="Open server panel",
            kind="process",
            build=lambda ctx: commands.build_server_gui_command(ctx.settings),
        ),
    ),
)

def _camera_targets(context: ActionContext) -> tuple[str, ...]:
    """The cameras step 2 works on, from its own form."""
    return resolve_camera_ids(
        context.settings.cameras,
        context.overview.camera_ids(),
        context.overview.local_camera_ids,
    )


CAMERAS_STEP = StepDefinition(
    id="cameras",
    index=2,
    title="Cameras",
    summary=(
        "Map video sources onto this PC's cameras, then set the field of view. "
        "Cameras owned by another PC keep the source that PC gave them."
    ),
    precondition=_needs_config,
    table="roster",
    fields=(
        FieldDefinition(
            "cameras",
            "Cameras to reconfigure",
            "text",
            help="Empty: the ones on this PC. 'all': the whole roster. Or 'cam_0 cam_2'.",
        ),
    ),
    actions=(
        ActionDefinition(
            id="select",
            label="Select sources",
            kind="process",
            build=lambda ctx: commands.build_select_cameras_command(
                ctx.settings, local_camera_ids=_camera_targets(ctx)
            ),
            enabled=lambda overview: overview.config_path is not None,
            disabled_hint="Select a configuration file first.",
        ),
        ActionDefinition(
            id="configure",
            label="Field of view",
            kind="process",
            build=lambda ctx: commands.build_configure_cameras_command(
                ctx.settings, camera_ids=_camera_targets(ctx)
            ),
            enabled=lambda overview: overview.config_path is not None,
            disabled_hint="Select a configuration file first.",
        ),
        ActionDefinition(
            id="probe",
            label="Probe sources",
            kind="task",
            build=lambda ctx: jobs.probe_job(ctx),
        ),
    ),
)

BOARD_STEP = StepDefinition(
    id="board",
    index=3,
    title="Board",
    summary="Generate the printable ChArUco board. Print it at 100% and check the 100 mm bar.",
    precondition=lambda overview: READY,
    fields=(
        FieldDefinition("board_format", "Board format", "choice", choices=("a4", "a3")),
        FieldDefinition("board_output", "Output folder", "dir"),
    ),
    actions=(
        ActionDefinition(
            id="generate",
            label="Generate board",
            kind="task",
            build=lambda ctx: jobs.board_job(ctx),
        ),
    ),
)

INTRINSICS_STEP = StepDefinition(
    id="intrinsics",
    index=4,
    title="Intrinsics",
    summary="Estimate each lens model, live with the guided wizard or from a folder of photos.",
    precondition=_needs_config,
    table="intrinsics",
    fields=(
        FieldDefinition(
            "photo_root",
            "Photo folder",
            "dir",
            help="One sub-folder per camera id, or a single folder for one camera.",
        ),
        FieldDefinition(
            "allow_low_quality", "Save even when the quality gates fail", "bool"
        ),
    ),
    actions=(
        ActionDefinition(
            id="wizard",
            label="Run wizard",
            kind="process",
            per_camera=True,
            build=lambda ctx: commands.build_intrinsics_command(ctx.settings, ctx.camera_id),
        ),
        ActionDefinition(
            id="from_folder",
            label="From folder",
            kind="task",
            build=lambda ctx: jobs.folder_calibration_job(ctx),
        ),
    ),
)

REFERENCES_STEP = StepDefinition(
    id="references",
    index=5,
    title="Reference markers",
    summary="Measure where the fixed markers are, and where the world origin sits.",
    precondition=_needs_config,
    fields=(
        FieldDefinition(
            "reference_markers", "Reference markers file", "path",
            help="Leave empty to use the markers stored in the configuration.",
        ),
    ),
    actions=(
        ActionDefinition(
            id="map",
            label="Map markers",
            kind="process",
            build=lambda ctx: commands.build_reference_map_command(ctx.settings),
        ),
        ActionDefinition(
            id="anchors",
            label="Pick anchors",
            kind="process",
            build=lambda ctx: commands.build_reference_map_command(ctx.settings, mode="anchors"),
        ),
        ActionDefinition(
            id="stitch",
            label="Stitch cameras",
            kind="process",
            build=lambda ctx: commands.build_reference_stitch_command(ctx.settings),
        ),
        ActionDefinition(
            id="origin",
            label="Set origin",
            kind="process",
            build=lambda ctx: commands.build_origin_command(ctx.settings),
        ),
    ),
)

EXTRINSICS_STEP = StepDefinition(
    id="extrinsics",
    index=6,
    title="Extrinsics",
    summary="Place each camera in world coordinates using the reference markers.",
    precondition=_extrinsics_ready,
    table="extrinsics",
    fields=(
        FieldDefinition("allow_low_quality", "Allow low quality (30 px RANSAC)", "bool"),
    ),
    actions=(
        ActionDefinition(
            id="wizard",
            label="Run wizard",
            kind="process",
            per_camera=True,
            build=lambda ctx: commands.build_extrinsics_command(ctx.settings, ctx.camera_id),
            # Local again: a remote camera's intrinsics are calibrated on its own
            # PC, and gating on them would grey out the button for good.
            enabled=lambda overview: not overview.local().missing("intrinsics"),
            disabled_hint="Calibrate the intrinsics of this PC's cameras first.",
        ),
    ),
)

RUNTIME_STEP = StepDefinition(
    id="runtime",
    index=7,
    title="Runtime",
    summary="Everything is calibrated: run the localizer, or open the server panel.",
    precondition=_runtime_ready,
    actions=(
        ActionDefinition(
            id="localizer",
            label="Run localizer",
            kind="process",
            build=lambda ctx: commands.build_localizer_command(ctx.settings),
        ),
        ActionDefinition(
            id="server_panel",
            label="Open server panel",
            kind="process",
            build=lambda ctx: commands.build_server_gui_command(ctx.settings),
        ),
    ),
)

CALIBRATION_STEPS: tuple[StepDefinition, ...] = (
    DEPLOYMENT_STEP,
    CAMERAS_STEP,
    BOARD_STEP,
    INTRINSICS_STEP,
    REFERENCES_STEP,
    EXTRINSICS_STEP,
    RUNTIME_STEP,
)

STEPS_BY_ID: Mapping[str, StepDefinition] = {step.id: step for step in CALIBRATION_STEPS}


def step(step_id: str) -> StepDefinition:
    return STEPS_BY_ID[step_id]


def expand(
    action: ActionDefinition, context: ActionContext, cameras: Sequence[str] = ()
) -> list[ActionContext]:
    """One context per run: a per-camera action covers the whole roster by default."""
    if not action.per_camera:
        return [context]
    # No explicit pick means "every camera I can actually reach from here".
    targets = tuple(cameras) or context.overview.local().camera_ids()
    return [
        ActionContext(settings=context.settings, overview=context.overview, camera_id=camera)
        for camera in targets
    ]
