"""Supervision of the spawned ``vision-server`` child process."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...core.queues import drain
from ...gui.process import CommandSpec, ProcessEventKind, ProcessRunner
from . import strings

DEFAULT_CACHE = Path(".state/last_good_config.json")
DEFAULT_CALIBRATIONS = Path("calibrations")
MAX_LOG_LINES = 2000
STOP_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ServerLaunchOptions:
    """Everything the panel needs to spawn one ``vision-server`` process."""

    config: Path | None = None
    calibrations: Path = DEFAULT_CALIBRATIONS
    cache: Path = DEFAULT_CACHE
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    debug: bool = True
    no_mqtt: bool = False
    verbose: bool = False


def build_server_command(
    options: ServerLaunchOptions, executable: str | None = None
) -> list[str]:
    """Build the ``vision-server`` command line, falling back to ``python -m``."""
    binary = executable or shutil.which("vision-server")
    command = [binary] if binary else [sys.executable, "-m", "vision_system.apps.coordinator"]
    if options.config is not None:
        command += ["--config", str(options.config)]
    command += ["--calibrations", str(options.calibrations), "--cache", str(options.cache)]
    if options.debug:
        command.append("--debug")
    if options.no_mqtt:
        command.append("--no-mqtt")
    if options.verbose:
        command.append("--verbose")
    return command


def build_server_environment(
    options: ServerLaunchOptions, environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Child environment: the broker is selected by variable, not by command line."""
    child = dict(os.environ if environment is None else environment)
    child["VISION_MQTT_HOST"] = options.mqtt_host
    child["VISION_MQTT_PORT"] = str(options.mqtt_port)
    child.setdefault("PYTHONUNBUFFERED", "1")
    return child


class ServerProcess:
    """The vision-server child, specifically: its command line and its log stream.

    The supervision mechanics live in gui.process.ProcessRunner, shared with the
    calibration panel; what stays here is the policy — which binary to run, which
    environment carries the broker, and what the panel prints about it.
    """

    def __init__(self, spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._runner = ProcessRunner(spawn=spawn)
        self._logs: queue.SimpleQueue[str] = queue.SimpleQueue()

    @property
    def running(self) -> bool:
        return self._runner.running

    @property
    def pid(self) -> int | None:
        return self._runner.pid

    @property
    def exit_code(self) -> int | None:
        return self._runner.exit_code

    def start(self, options: ServerLaunchOptions, cwd: Path | None = None) -> list[str]:
        if self.running:
            raise RuntimeError(strings.ALREADY_RUNNING)
        command = build_server_command(options)
        self._runner.start(
            CommandSpec(
                argv=tuple(command),
                title="vision-server",
                env=build_server_environment(options),
                cwd=cwd,
            )
        )
        return command

    def drain_logs(self, limit: int = MAX_LOG_LINES) -> list[str]:
        """Console lines, with the child's exit folded in as one more line.

        Keeping the exit notice in the same stream is deliberate: it has to appear
        interleaved with the server's own output, in the order it happened.
        """
        for event in self._runner.drain(limit):
            if event.kind is ProcessEventKind.EXITED:
                self._logs.put(strings.SERVER_EXIT_LINE.format(code=event.exit_code))
            else:
                self._logs.put(event.message)
        return drain(self._logs, limit)

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        self._runner.stop(timeout=timeout)
