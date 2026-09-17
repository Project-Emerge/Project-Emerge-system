"""Supervision of the spawned ``vision-server`` child process."""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ...core.queues import drain
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
    """Owns the spawned ``vision-server`` process and its captured console output."""

    def __init__(self, spawn: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._spawn = spawn
        self._process: subprocess.Popen | None = None
        self._logs: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._reader: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def exit_code(self) -> int | None:
        return self._process.poll() if self._process is not None else None

    def start(self, options: ServerLaunchOptions, cwd: Path | None = None) -> list[str]:
        if self.running:
            raise RuntimeError(strings.ALREADY_RUNNING)
        command = build_server_command(options)
        self._process = self._spawn(
            command,
            cwd=None if cwd is None else str(cwd),
            env=build_server_environment(options),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            # Own session: closing the panel or hitting Ctrl-C in the terminal that
            # started it must not take the server down behind the user's back.
            start_new_session=True,
        )
        self._logs.put(f"$ {' '.join(command)}")
        self._reader = threading.Thread(
            target=self._pump, args=(self._process,), name="vision-server-logs", daemon=True
        )
        self._reader.start()
        return command

    def _pump(self, process: subprocess.Popen) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                self._logs.put(line.rstrip("\n"))
        self._logs.put(strings.SERVER_EXIT_LINE.format(code=process.wait()))

    def drain_logs(self, limit: int = MAX_LOG_LINES) -> list[str]:
        return drain(self._logs, limit)

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._logs.put(strings.SIGKILL_LINE)
            process.kill()
            process.wait(timeout=timeout)
