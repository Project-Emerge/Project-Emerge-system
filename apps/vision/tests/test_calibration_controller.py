"""The calibration panel's behaviour, with fake runners and no display."""

from pathlib import Path

import pytest

from vision_system.apps.calibration_gui.controller import (
    CalibrationController,
    NoticeCode,
)
from vision_system.apps.calibration_gui.settings import GuiSettings
from vision_system.apps.calibration_gui.status import CalibrationOverview, CameraStatus
from vision_system.gui.process import CommandSpec, ProcessEvent, ProcessEventKind
from vision_system.gui.tasks import TaskRunner

CALIBRATIONS = Path("calibrations")


class FakeQueue:
    """Stands in for ProcessQueue; records what was asked for, runs nothing."""

    def __init__(self) -> None:
        self.enqueued: list[CommandSpec] = []
        self.events: list[ProcessEvent] = []
        self.cancelled = 0
        self.busy = False

    def enqueue(self, specs):
        self.enqueued.extend(specs)

    def poll(self):
        events, self.events = self.events, []
        return events

    def cancel(self):
        self.cancelled += 1
        self.enqueued.clear()


def _status(camera_id, *, intrinsics=True, extrinsics=False, stale=False):
    return CameraStatus(
        camera_id=camera_id,
        source=0,
        has_intrinsics=intrinsics,
        intrinsic_quality_passed=True if intrinsics else None,
        has_extrinsics=extrinsics,
        extrinsic_quality_passed=True if extrinsics else None,
        geometry_error="digital zoom differs" if stale else None,
    )


def _overview(*cameras, references=9, config=Path("c.json")):
    return CalibrationOverview(
        cameras=cameras,
        calibrations_dir=CALIBRATIONS,
        config_path=config,
        reference_marker_count=references,
    )


def _controller(*cameras, overview=None, **kwargs):
    view = overview if overview is not None else _overview(*cameras)
    queue = kwargs.pop("processes", None) or FakeQueue()
    controller = CalibrationController(
        GuiSettings(config_path=Path("c.json")),
        tasks=TaskRunner(executor=lambda run: run()),
        processes=queue,
        overview_loader=lambda settings: view,
        **kwargs,
    )
    controller.refresh()
    controller.poll()          # drop the refresh notice
    return controller


def _codes(notices):
    return [notice.code for notice in notices]


def test_the_panel_starts_on_the_first_step():
    controller = _controller(_status("cam_0"))
    assert controller.current_step.id == "cameras"
    assert [step.id for step in controller.steps][0] == "cameras"


def test_an_unreadable_configuration_leaves_an_empty_table_not_a_crash():
    def explode(settings):
        raise OSError("no such file")

    controller = CalibrationController(
        GuiSettings(), overview_loader=explode, processes=FakeQueue()
    )
    controller.refresh()
    _, notices = controller.poll()
    assert _codes(notices) == [NoticeCode.CONFIG_UNREADABLE]
    assert controller.overview.cameras == ()


@pytest.mark.parametrize(
    "cameras",
    [("cam_0", "cam_1"), ("cam_0", "cam_1", "cam_2"), ("cam_0", "cam_1", "cam_2", "cam_3")],
)
def test_the_wizard_is_queued_once_per_configured_camera(cameras):
    queue = FakeQueue()
    controller = _controller(*(_status(name) for name in cameras), processes=queue)
    controller.trigger("intrinsics", "wizard")
    assert [spec.camera_id for spec in queue.enqueued] == list(cameras)
    assert all("--camera" in spec.argv for spec in queue.enqueued)


def test_the_wizard_can_be_run_for_a_single_camera():
    queue = FakeQueue()
    controller = _controller(_status("cam_0"), _status("cam_1"), processes=queue)
    controller.trigger("intrinsics", "wizard", cameras=("cam_1",))
    assert [spec.camera_id for spec in queue.enqueued] == ["cam_1"]


def test_a_blocked_step_explains_itself_instead_of_running():
    queue = FakeQueue()
    controller = _controller(_status("cam_0", intrinsics=False), processes=queue)
    controller.trigger("extrinsics", "wizard")
    _, notices = controller.poll()
    assert NoticeCode.STEP_BLOCKED in _codes(notices)
    assert queue.enqueued == [], "nothing may be launched behind a closed gate"


def test_a_stale_camera_blocks_extrinsics_with_the_right_reason():
    controller = _controller(_status("cam_0", stale=True))
    controller.trigger("extrinsics", "wizard")
    _, notices = controller.poll()
    blocked = next(n for n in notices if n.code is NoticeCode.STEP_BLOCKED)
    assert "settings changed" in blocked.fields["reason"]


def test_only_one_operation_runs_at_a_time():
    queue = FakeQueue()
    controller = _controller(_status("cam_0"), processes=queue)
    controller.trigger("intrinsics", "wizard")
    queue.busy = True
    controller.trigger("intrinsics", "wizard")
    _, notices = controller.poll()
    assert NoticeCode.ALREADY_BUSY in _codes(notices)
    assert len(queue.enqueued) == 1


def test_an_unknown_action_is_a_programming_error():
    controller = _controller(_status("cam_0"))
    with pytest.raises(KeyError):
        controller.trigger("intrinsics", "no-such-button")


def test_a_failing_task_is_reported_and_does_not_escape_into_the_view():
    controller = _controller(_status("cam_0"))
    from vision_system.apps.calibration_gui import jobs

    def explode(context):
        from vision_system.gui.tasks import TaskSpec

        def run(token, emit):
            raise ValueError("no camera photo folders found under photo")

        return TaskSpec(id="intrinsics.from_folder", title="Calibrating", run=run)

    original = jobs.folder_calibration_job
    jobs.folder_calibration_job = explode
    try:
        controller.trigger("intrinsics", "from_folder")   # must not raise
        _, notices = controller.poll()                    # must not raise either
    finally:
        jobs.folder_calibration_job = original
    failure = next(n for n in notices if n.code is NoticeCode.TASK_FAILED)
    assert "no camera photo folders" in failure.fields["error"]
    assert controller.busy is False


def test_the_table_is_reread_after_a_command_exits_whatever_its_code():
    # Cancelling a wizard exits non-zero, and a run that failed on the third camera
    # still wrote the first two, so the exit code is never the verdict.
    loads = []
    view = _overview(_status("cam_0"))
    queue = FakeQueue()
    controller = CalibrationController(
        GuiSettings(config_path=Path("c.json")),
        tasks=TaskRunner(executor=lambda run: run()),
        processes=queue,
        overview_loader=lambda settings: (loads.append(1), view)[1],
    )
    controller.refresh()
    controller.poll()
    before = len(loads)
    queue.events = [
        ProcessEvent(
            ProcessEventKind.EXITED,
            CommandSpec(argv=("x",), title="intrinsics cam_0"),
            exit_code=1,
        )
    ]
    _, notices = controller.poll()
    assert len(loads) == before + 1
    assert NoticeCode.COMMAND_FINISHED in _codes(notices)


def test_cancelling_stops_both_runners():
    queue = FakeQueue()
    controller = _controller(_status("cam_0"), processes=queue)
    controller.cancel()
    _, notices = controller.poll()
    assert queue.cancelled == 1
    assert NoticeCode.CANCELLED in _codes(notices)


def test_selecting_a_step_changes_the_current_one():
    controller = _controller(_status("cam_0"))
    assert controller.select_step("board").id == "board"
    assert controller.current_step.id == "board"


def test_changing_the_configuration_rereads_the_table():
    loads = []
    controller = CalibrationController(
        GuiSettings(),
        processes=FakeQueue(),
        overview_loader=lambda settings: (loads.append(settings.config_path), _overview())[1],
    )
    controller.set_config_path(Path("other.json"))
    assert loads == [Path("other.json")]
    assert controller.settings.config_path == Path("other.json")
