"""Running an external command and streaming its output into a panel.

The calibration stages that open an OpenCV window run as child processes rather
than in-process: HighGUI has hard thread affinity (Cocoa wants the main thread,
GTK/Qt are not thread-safe and open their own display connection), and a wizard
loop only ends on a key press, so there would be no way to cancel one. A child
owns its main thread, dies without taking the panel with it, and is cancellable
with the usual signal ladder.
"""

from __future__ import annotations

import os
import queue
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..core.queues import drain

DEFAULT_STOP_TIMEOUT_S = 10.0
MAX_OUTPUT_LINES = 2000
SIGKILL_NOTICE = "[the process ignored SIGTERM: killing it]"


@dataclass(frozen=True)
class CommandSpec:
    """One command to run, and what the panel should say about it."""

    argv: tuple[str, ...]
    title: str
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    # Which camera this command needs exclusive access to, if any. V4L2 nodes are
    # single-open, so two jobs touching one camera must not overlap.
    camera_id: str | None = None

    def command_line(self) -> str:
        return " ".join(self.argv)


class ProcessEventKind(StrEnum):
    STARTED = "started"
    LOG = "log"
    EXITED = "exited"


@dataclass(frozen=True)
class ProcessEvent:
    """Something that happened to a child process."""

    kind: ProcessEventKind
    spec: CommandSpec
    message: str = ""
    exit_code: int | None = None


class ProcessRunner:
    """Owns at most one child process and its captured output."""

    def __init__(self, spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._spawn = spawn
        # start() is called from the UI thread while stop() may be submitted to a
        # worker, so the two must not interleave around self._process.
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._events: queue.SimpleQueue[ProcessEvent] = queue.SimpleQueue()
        self._reader: threading.Thread | None = None
        self._spec: CommandSpec | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def exit_code(self) -> int | None:
        return self._process.poll() if self._process is not None else None

    @property
    def spec(self) -> CommandSpec | None:
        return self._spec

    def start(self, spec: CommandSpec) -> None:
        with self._lock:
            if self.running:
                raise RuntimeError("a command is already running")
            self._spec = spec
            environment = dict(os.environ)
            environment.update(spec.env)
            environment.setdefault("PYTHONUNBUFFERED", "1")
            self._process = self._spawn(
                list(spec.argv),
                cwd=None if spec.cwd is None else str(spec.cwd),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # Own session: closing the panel must not take a running wizard down
                # behind the operator's back.
                start_new_session=True,
            )
            self._events.put(
                ProcessEvent(ProcessEventKind.STARTED, spec, message=f"$ {spec.command_line()}")
            )
            self._reader = threading.Thread(
                target=self._pump,
                args=(self._process, spec),
                name=f"vision-gui-{spec.title}",
                daemon=True,
            )
            self._reader.start()

    def _pump(self, process: subprocess.Popen, spec: CommandSpec) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self._events.put(
                    ProcessEvent(ProcessEventKind.LOG, spec, message=line.rstrip("\n"))
                )
        code = process.wait()
        self._events.put(ProcessEvent(ProcessEventKind.EXITED, spec, exit_code=code))

    def drain(self, limit: int = MAX_OUTPUT_LINES) -> list[ProcessEvent]:
        return drain(self._events, limit)

    def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT_S) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if self._spec is not None:
                    self._events.put(
                        ProcessEvent(ProcessEventKind.LOG, self._spec, message=SIGKILL_NOTICE)
                    )
                process.kill()
                process.wait(timeout=timeout)


class ProcessQueue:
    """Runs a list of commands one at a time.

    This is how "calibrate every camera" works: the wizards each want the display
    and a camera to themselves, so they are queued rather than launched together.
    """

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        self.runner = runner if runner is not None else ProcessRunner()
        self._pending: list[CommandSpec] = []

    @property
    def pending(self) -> int:
        return len(self._pending)

    @property
    def busy(self) -> bool:
        return self.runner.running or bool(self._pending)

    def enqueue(self, specs: Sequence[CommandSpec]) -> None:
        self._pending.extend(specs)

    def poll(self) -> list[ProcessEvent]:
        """Drain the current child and start the next one once it has exited."""
        events = self.runner.drain()
        finished = any(event.kind is ProcessEventKind.EXITED for event in events)
        if (finished or not self.runner.running) and self._pending:
            events.extend(self._start_next())
        return events

    def _start_next(self) -> list[ProcessEvent]:
        spec = self._pending.pop(0)
        try:
            self.runner.start(spec)
        except (OSError, RuntimeError) as error:
            return [
                ProcessEvent(
                    ProcessEventKind.EXITED, spec, message=str(error), exit_code=None
                )
            ]
        return self.runner.drain()

    def cancel(self) -> None:
        """Drop everything still queued and terminate whatever is running."""
        self._pending.clear()
        self.runner.stop()
