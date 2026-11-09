"""Panel behaviour for the calibration workflow, with no widget in sight.

It owns the settings, the current overview and the two runners, and turns a
button press into either a queued command or a background task. Like the server
panel's controller it formats nothing and imports no Tk, so the whole feature is
reachable from a test with fake runners.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from ...calibration.board import BoardSpec
from ...calibration.store import load_calibrations
from ...core.config import initial_config
from ...core.setup import load_setup
from ...gui.process import CommandSpec, ProcessEventKind, ProcessQueue
from ...gui.tasks import TaskEventKind, TaskRunner, TaskSpec
from . import steps
from .settings import GuiSettings
from .status import CalibrationOverview, describe_calibrations
from .steps import ActionContext, StepDefinition


class NoticeCode(StrEnum):
    """Something the operator should be told, named rather than worded."""

    NO_CONFIG = "no_config"
    CONFIG_UNREADABLE = "config_unreadable"
    CONFIG_ABSENT = "config_absent"
    SETUP_UNREADABLE = "setup_unreadable"
    REFRESHED = "refreshed"
    STEP_BLOCKED = "step_blocked"
    ACTION_BLOCKED = "action_blocked"
    ALREADY_BUSY = "already_busy"
    CAMERA_BUSY = "camera_busy"
    QUEUED = "queued"
    TASK_STARTED = "task_started"
    TASK_FINISHED = "task_finished"
    TASK_FAILED = "task_failed"
    CANCELLED = "cancelled"
    COMMAND_FINISHED = "command_finished"
    NOTHING_TO_RUN = "nothing_to_run"


@dataclass(frozen=True)
class Notice:
    code: NoticeCode
    fields: dict[str, object] = field(default_factory=dict)


def read_overview(settings: GuiSettings) -> CalibrationOverview:
    """Load the configuration and the artifacts, then derive the table."""
    config = initial_config(settings.config_path, settings.cache_path)
    calibrations = load_calibrations(settings.calibrations_dir)
    return describe_calibrations(
        config,
        calibrations,
        calibrations_dir=settings.calibrations_dir,
        config_path=settings.config_path,
        board_spec=BoardSpec.for_format(settings.board_format),
        local_camera_ids=settings.local_camera_ids(config),
    )


EMPTY_OVERVIEW = CalibrationOverview(cameras=(), calibrations_dir=Path("calibrations"))


class CalibrationController:
    """Owns the panel's state and turns operator intent into runnable work."""

    def __init__(
        self,
        settings: GuiSettings,
        *,
        tasks: TaskRunner | None = None,
        processes: ProcessQueue | None = None,
        overview_loader: Callable[[GuiSettings], CalibrationOverview] = read_overview,
    ) -> None:
        self.settings = settings
        self.tasks = tasks if tasks is not None else TaskRunner()
        self.processes = processes if processes is not None else ProcessQueue()
        self._load_overview = overview_loader
        self.overview = EMPTY_OVERVIEW
        self.current_step_id = steps.CALIBRATION_STEPS[0].id
        self._notices: list[Notice] = []

    # ---------------------------------------------------------------- queries
    @property
    def steps(self) -> tuple[StepDefinition, ...]:
        return steps.CALIBRATION_STEPS

    @property
    def current_step(self) -> StepDefinition:
        return steps.step(self.current_step_id)

    @property
    def busy(self) -> bool:
        return self.tasks.busy or self.processes.busy

    def readiness(self, step: StepDefinition):
        return step.precondition(self.overview)

    # ---------------------------------------------------------------- actions
    def select_step(self, step_id: str) -> StepDefinition:
        self.current_step_id = steps.step(step_id).id
        return self.current_step

    def set_config_path(self, path: Path | None) -> None:
        self.settings.config_path = path
        self.load_setup()
        self.refresh()

    def load_setup(self) -> None:
        """Adopt the deployment preferences saved next to the configuration.

        Read here rather than in ``read_overview`` because the overview is
        re-derived after every stage: re-reading the file each time would throw
        away edits the operator has typed but not yet saved.
        """
        if self.settings.config_path is None:
            return
        try:
            self.settings.setup = load_setup(self.settings.config_path)
        except (OSError, ValueError) as error:
            self._notices.append(Notice(NoticeCode.SETUP_UNREADABLE, {"error": error}))

    def refresh(self) -> None:
        """Re-read the artifacts from disk.

        Deliberately not via CalibrationStore: that throttles rescans to two
        seconds, which is exactly the window in which a wizard has just exited and
        the operator is looking at the table.
        """
        path = self.settings.config_path
        try:
            self.overview = self._load_overview(self.settings)
        except (OSError, ValueError) as error:
            # Keep the path: an empty table is not an unknown file, and step 1 is
            # precisely the step that creates the configuration that is missing.
            self.overview = replace(EMPTY_OVERVIEW, config_path=path)
            absent = path is not None and not path.exists()
            self._notices.append(
                Notice(NoticeCode.CONFIG_ABSENT, {"path": path})
                if absent
                else Notice(NoticeCode.CONFIG_UNREADABLE, {"error": error})
            )
            return
        # The count follows the roster on disk until the operator changes it, so
        # saving without touching the field cannot resize anything by accident.
        self.settings.roster = self.overview.camera_ids()
        self._notices.append(
            Notice(
                NoticeCode.REFRESHED,
                {
                    "cameras": list(self.overview.camera_ids()),
                    "local": list(self.overview.local().camera_ids()),
                },
            )
        )

    def trigger(self, step_id: str, action_id: str, cameras: Sequence[str] = ()) -> None:
        """Run one action, expanded over the roster when it is per-camera."""
        step = steps.step(step_id)
        action = next((a for a in step.actions if a.id == action_id), None)
        if action is None:
            raise KeyError(f"{step_id} has no action {action_id}")

        readiness = step.precondition(self.overview)
        if not readiness.ready:
            self._notices.append(Notice(NoticeCode.STEP_BLOCKED, {"reason": readiness.reason}))
            return
        if not action.enabled(self.overview):
            self._notices.append(
                Notice(NoticeCode.ACTION_BLOCKED, {"reason": action.disabled_hint})
            )
            return
        if self.busy:
            self._notices.append(Notice(NoticeCode.ALREADY_BUSY))
            return

        context = ActionContext(settings=self.settings, overview=self.overview)
        contexts = steps.expand(action, context, cameras)
        if not contexts:
            self._notices.append(Notice(NoticeCode.NOTHING_TO_RUN))
            return
        built = [action.build(ctx) for ctx in contexts]
        if action.kind == "process":
            self._enqueue([spec for spec in built if isinstance(spec, CommandSpec)])
        else:
            self._submit(built[0])

    def _enqueue(self, specs: list[CommandSpec]) -> None:
        self.processes.enqueue(specs)
        self._notices.append(
            Notice(NoticeCode.QUEUED, {"titles": [spec.title for spec in specs]})
        )

    def _submit(self, spec: TaskSpec) -> None:
        try:
            self.tasks.submit(spec)
        except RuntimeError:
            self._notices.append(Notice(NoticeCode.ALREADY_BUSY))

    def cancel(self) -> None:
        self.tasks.cancel()
        self.processes.cancel()
        self._notices.append(Notice(NoticeCode.CANCELLED))

    # ------------------------------------------------------------------- tick
    def poll(self) -> tuple[list[str], list[Notice]]:
        """Drain both runners into console lines plus notices. Formats nothing."""
        lines: list[str] = []
        notices, self._notices = self._notices, []
        refresh_needed = False

        for event in self.processes.poll():
            if event.kind is ProcessEventKind.EXITED:
                # The exit code cannot be trusted as a verdict: cancelling a wizard
                # exits non-zero, and a multi-camera run that failed on the third
                # camera still wrote the first two. The table is the truth.
                notices.append(
                    Notice(
                        NoticeCode.COMMAND_FINISHED,
                        {"title": event.spec.title, "code": event.exit_code},
                    )
                )
                if event.message:
                    lines.append(event.message)
                refresh_needed = True
            else:
                lines.append(event.message)

        for event in self.tasks.drain():
            if event.kind is TaskEventKind.LOG:
                lines.append(event.message)
            elif event.kind is TaskEventKind.STARTED:
                notices.append(Notice(NoticeCode.TASK_STARTED, {"title": event.message}))
            elif event.kind is TaskEventKind.FINISHED:
                notices.append(Notice(NoticeCode.TASK_FINISHED, {"payload": event.payload}))
                refresh_needed = True
            elif event.kind is TaskEventKind.FAILED:
                notices.append(Notice(NoticeCode.TASK_FAILED, {"error": event.message}))
            elif event.kind is TaskEventKind.CANCELLED:
                notices.append(Notice(NoticeCode.CANCELLED))
                refresh_needed = True

        if refresh_needed and not self.busy:
            self.refresh()
            notices.extend(self._notices)
            self._notices = []
        return lines, notices

    def shutdown(self) -> None:
        self.tasks.cancel()
        self.processes.cancel()
