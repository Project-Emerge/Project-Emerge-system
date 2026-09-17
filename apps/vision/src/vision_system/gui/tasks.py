"""Running pure-Python work off the UI thread, with cancellation and progress.

Board generation, camera probing and folder calibration are plain Python that
returns rich dataclasses. Running them as subprocesses would flatten those results
back into printed text, so they run here instead, on a worker thread that reports
through a queue the UI drains on its tick.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ..core.queues import drain

MAX_TASK_EVENTS = 256


class TaskCancelled(Exception):
    """Raised inside a task body when the operator asked it to stop."""


class CancelToken:
    """Cooperative cancellation: a task checks it between units of work."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelled


class TaskEventKind(StrEnum):
    STARTED = "started"
    LOG = "log"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskEvent:
    task_id: str
    kind: TaskEventKind
    message: str = ""
    payload: object | None = None


@dataclass(frozen=True)
class TaskSpec:
    """A unit of background work: an id, a title and a body to run."""

    id: str
    title: str
    run: Callable[[CancelToken, Callable[[str], None]], object]


def _daemon_thread(function: Callable[[], None]) -> None:
    threading.Thread(target=function, name="vision-gui-task", daemon=True).start()


class TaskRunner:
    """Runs one task at a time and reports through a queue.

    ``executor`` is the test seam: pass ``lambda run: run()`` and everything happens
    inline, so a controller test needs no threads and no sleeps.
    """

    def __init__(self, executor: Callable[[Callable[[], None]], None] = _daemon_thread) -> None:
        self._executor = executor
        self._events: queue.SimpleQueue[TaskEvent] = queue.SimpleQueue()
        self._token: CancelToken | None = None
        self._busy = threading.Event()

    @property
    def busy(self) -> bool:
        return self._busy.is_set()

    def submit(self, spec: TaskSpec) -> CancelToken:
        if self.busy:
            raise RuntimeError("another operation is already running")
        token = CancelToken()
        self._token = token
        self._busy.set()
        self._events.put(TaskEvent(spec.id, TaskEventKind.STARTED, message=spec.title))
        self._executor(lambda: self._run(spec, token))
        return token

    def _run(self, spec: TaskSpec, token: CancelToken) -> None:
        def emit(message: str) -> None:
            self._events.put(TaskEvent(spec.id, TaskEventKind.LOG, message=message))

        try:
            payload = spec.run(token, emit)
        except TaskCancelled:
            self._events.put(TaskEvent(spec.id, TaskEventKind.CANCELLED))
        except Exception as error:  # noqa: BLE001 - a task must never kill the panel
            self._events.put(TaskEvent(spec.id, TaskEventKind.FAILED, message=str(error)))
        else:
            if token.cancelled:
                self._events.put(TaskEvent(spec.id, TaskEventKind.CANCELLED))
            else:
                self._events.put(
                    TaskEvent(spec.id, TaskEventKind.FINISHED, payload=payload)
                )
        finally:
            self._busy.clear()

    def drain(self, limit: int = MAX_TASK_EVENTS) -> list[TaskEvent]:
        return drain(self._events, limit)

    def cancel(self) -> None:
        if self._token is not None:
            self._token.cancel()
