"""Background work: results, failures, cancellation and progress."""

import threading
import time

import pytest

from vision_system.gui.tasks import (
    CancelToken,
    TaskEventKind,
    TaskRunner,
    TaskSpec,
)


def inline() -> TaskRunner:
    """A runner that executes submitted work immediately, on the calling thread."""
    return TaskRunner(executor=lambda run: run())


def _spec(body, task_id: str = "job", title: str = "Doing a thing") -> TaskSpec:
    return TaskSpec(id=task_id, title=title, run=body)


def _kinds(events):
    return [event.kind for event in events]


def test_a_successful_task_reports_its_result():
    runner = inline()
    runner.submit(_spec(lambda token, emit: {"written": 3}))
    events = runner.drain()
    assert _kinds(events) == [TaskEventKind.STARTED, TaskEventKind.FINISHED]
    assert events[1].payload == {"written": 3}


def test_progress_messages_arrive_in_order():
    def body(token, emit):
        emit("cam_0: 42 images")
        emit("cam_1: 37 images")
        return None

    runner = inline()
    runner.submit(_spec(body))
    messages = [e.message for e in runner.drain() if e.kind is TaskEventKind.LOG]
    assert messages == ["cam_0: 42 images", "cam_1: 37 images"]


def test_a_failing_task_is_reported_and_never_reaches_the_caller():
    def body(token, emit):
        raise ValueError("no camera subfolders found")

    runner = inline()
    runner.submit(_spec(body))          # must not raise
    events = runner.drain()
    assert _kinds(events) == [TaskEventKind.STARTED, TaskEventKind.FAILED]
    assert "no camera subfolders found" in events[1].message
    assert runner.busy is False


def test_a_cancelled_task_is_reported_as_cancelled_not_failed():
    def body(token, emit):
        token.raise_if_cancelled()
        return "unreachable"

    runner = inline()
    token = CancelToken()
    token.cancel()
    spec = _spec(lambda t, emit: body(token, emit))
    runner.submit(spec)
    assert _kinds(runner.drain())[-1] is TaskEventKind.CANCELLED


def test_cancelling_between_units_of_work_stops_the_rest():
    processed = []

    def body(token, emit):
        for camera in ("cam_0", "cam_1", "cam_2"):
            token.raise_if_cancelled()
            processed.append(camera)
            if camera == "cam_0":
                token.cancel()          # as if the operator pressed Cancel
        return processed

    runner = inline()
    runner.submit(_spec(body))
    assert processed == ["cam_0"], "the loop must stop at the next checkpoint"
    assert _kinds(runner.drain())[-1] is TaskEventKind.CANCELLED


def test_a_task_that_finished_after_being_cancelled_still_counts_as_cancelled():
    runner = inline()

    def body(token, emit):
        token.cancel()
        return "done anyway"

    runner.submit(_spec(body))
    assert _kinds(runner.drain())[-1] is TaskEventKind.CANCELLED


def test_only_one_task_runs_at_a_time():
    started = threading.Event()
    release = threading.Event()

    def body(token, emit):
        started.set()
        release.wait(5.0)
        return None

    runner = TaskRunner()               # real threads here, on purpose
    runner.submit(_spec(body))
    assert started.wait(5.0)
    try:
        with pytest.raises(RuntimeError):
            runner.submit(_spec(lambda token, emit: None))
    finally:
        release.set()
    deadline = time.monotonic() + 5.0
    while runner.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.busy is False


def test_the_runner_is_reusable_once_the_previous_task_ended():
    runner = inline()
    runner.submit(_spec(lambda token, emit: 1))
    runner.submit(_spec(lambda token, emit: 2))
    payloads = [e.payload for e in runner.drain() if e.kind is TaskEventKind.FINISHED]
    assert payloads == [1, 2]
