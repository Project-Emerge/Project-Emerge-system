"""Supervising child processes and running a queue of them one at a time."""

import sys
import threading
import time

import pytest

from vision_system.gui.process import (
    CommandSpec,
    ProcessEventKind,
    ProcessQueue,
    ProcessRunner,
)


def _spec(script: str, **overrides) -> CommandSpec:
    payload = dict(argv=(sys.executable, "-c", script), title="probe")
    payload.update(overrides)
    return CommandSpec(**payload)


def _drain_until(runner_or_queue, kind, timeout_s: float = 10.0):
    collected = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        collected.extend(runner_or_queue.drain() if hasattr(runner_or_queue, "drain")
                         else runner_or_queue.poll())
        if any(event.kind is kind for event in collected):
            return collected
        time.sleep(0.02)
    pytest.fail(f"no {kind} event within {timeout_s}s; got {collected}")


def test_runner_streams_output_and_reports_the_exit_code():
    runner = ProcessRunner()
    runner.start(_spec("print('hello'); raise SystemExit(3)"))
    events = _drain_until(runner, ProcessEventKind.EXITED)
    assert events[0].kind is ProcessEventKind.STARTED
    assert any(e.kind is ProcessEventKind.LOG and e.message == "hello" for e in events)
    exited = next(e for e in events if e.kind is ProcessEventKind.EXITED)
    assert exited.exit_code == 3


def test_runner_merges_stderr_into_the_same_stream():
    runner = ProcessRunner()
    runner.start(_spec("import sys; print('boom', file=sys.stderr)"))
    events = _drain_until(runner, ProcessEventKind.EXITED)
    assert any("boom" in event.message for event in events)


def test_runner_refuses_to_start_a_second_command():
    runner = ProcessRunner()
    runner.start(_spec("import time; time.sleep(5)"))
    try:
        with pytest.raises(RuntimeError):
            runner.start(_spec("print('never')"))
    finally:
        runner.stop(timeout=5.0)


def test_runner_stop_is_safe_before_start_and_after_exit():
    runner = ProcessRunner()
    runner.stop(timeout=1.0)                  # nothing started yet
    runner.start(_spec("raise SystemExit(0)"))
    _drain_until(runner, ProcessEventKind.EXITED)
    runner.stop(timeout=1.0)                  # already gone
    assert runner.exit_code == 0


def test_runner_terminates_a_command_that_will_not_finish():
    runner = ProcessRunner()
    runner.start(_spec("import time; time.sleep(30)"))
    assert runner.running
    runner.stop(timeout=5.0)
    assert not runner.running


def test_runner_passes_extra_environment_to_the_child():
    runner = ProcessRunner()
    script = "import os; print(os.environ['VISION_PROBE'])"
    runner.start(_spec(script, env={"VISION_PROBE": "set-by-panel"}))
    events = _drain_until(runner, ProcessEventKind.EXITED)
    assert any(event.message == "set-by-panel" for event in events)


def test_runner_inherits_the_parent_environment(monkeypatch):
    # DISPLAY and WAYLAND_DISPLAY have to reach the child or no window opens.
    monkeypatch.setenv("VISION_PARENT_ONLY", "inherited")
    runner = ProcessRunner()
    runner.start(_spec("import os; print(os.environ['VISION_PARENT_ONLY'])"))
    events = _drain_until(runner, ProcessEventKind.EXITED)
    assert any(event.message == "inherited" for event in events)


class FakePopen:
    """Records what it was asked to run; never touches the OS."""

    instances: list["FakePopen"] = []

    def __init__(self, argv, **kwargs):
        self.argv = argv
        self.kwargs = kwargs
        self.stdout = iter(())
        self.terminated = False
        self._code: int | None = None
        type(self).instances.append(self)

    def poll(self):
        return self._code

    def wait(self, timeout=None):
        self._code = 0
        return 0

    def terminate(self):
        self.terminated = True
        self._code = -15

    def kill(self):
        self._code = -9

    @property
    def pid(self):
        return 1234


def test_the_child_runs_in_its_own_session():
    # Closing the panel or Ctrl-C in its terminal must not kill a running wizard.
    FakePopen.instances.clear()
    runner = ProcessRunner(spawn=FakePopen)
    runner.start(_spec("print(1)"))
    assert FakePopen.instances[0].kwargs["start_new_session"] is True


def test_queue_runs_commands_one_after_the_other():
    FakePopen.instances.clear()
    queue = ProcessQueue(ProcessRunner(spawn=FakePopen))
    queue.enqueue([_spec("print(1)", camera_id="cam_0"), _spec("print(2)", camera_id="cam_1")])
    assert queue.pending == 2
    queue.poll()
    assert len(FakePopen.instances) == 1, "only the first command may start"
    assert queue.pending == 1
    queue.poll()
    assert len(FakePopen.instances) == 2
    assert queue.pending == 0


class BlockingPopen(FakePopen):
    """Stays alive until terminated, so cancellation has something to cancel."""

    def __init__(self, argv, **kwargs):
        super().__init__(argv, **kwargs)
        self._done = threading.Event()

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return self._code

    def terminate(self):
        self.terminated = True
        self._code = -15
        self._done.set()


def test_cancelling_the_queue_drops_what_has_not_started():
    BlockingPopen.instances = []
    runner = ProcessRunner(spawn=BlockingPopen)
    queue = ProcessQueue(runner)
    queue.enqueue([_spec("print(1)"), _spec("print(2)"), _spec("print(3)")])
    queue.poll()
    assert queue.pending == 2
    queue.cancel()
    assert queue.pending == 0
    assert BlockingPopen.instances[0].terminated is True
    assert len(BlockingPopen.instances) == 1, "the queued commands must never start"


def test_a_command_that_cannot_be_spawned_is_reported_not_raised():
    def refuse(argv, **kwargs):
        raise OSError("vision-calibrate not found")

    queue = ProcessQueue(ProcessRunner(spawn=refuse))
    queue.enqueue([_spec("print(1)")])
    events = queue.poll()
    assert [event.kind for event in events] == [ProcessEventKind.EXITED]
    assert "not found" in events[0].message
