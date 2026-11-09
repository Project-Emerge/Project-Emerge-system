"""Building the vision-server command line and supervising the child process."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from vision_system.apps.server_gui.supervisor import (
    ServerLaunchOptions,
    ServerProcess,
    build_server_command,
    build_server_environment,
)


def _options(**overrides) -> ServerLaunchOptions:
    defaults = {
        "config": Path("config.local.json"),
        "calibrations": Path("calibrations"),
        "cache": Path(".state/last_good_config.json"),
        "mqtt_host": "192.168.1.10",
        "mqtt_port": 1884,
    }
    return ServerLaunchOptions(**{**defaults, **overrides})



def _runs(script: str):
    """A spawn that ignores the real command line and runs `script` instead."""

    def spawn(argv, **kwargs):
        return subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            text=True,
        )

    return spawn


def test_command_uses_console_script_and_flags():
    command = build_server_command(_options(verbose=True), executable="/usr/bin/vision-server")
    assert command[0] == "/usr/bin/vision-server"
    assert command[1:3] == ["--config", "config.local.json"]
    assert "--debug" in command and "--verbose" in command
    assert "--no-mqtt" not in command


def test_command_falls_back_to_module_when_script_missing(monkeypatch):
    monkeypatch.setattr("vision_system.apps.server_gui.supervisor.shutil.which", lambda name: None)
    command = build_server_command(_options(config=None, debug=False, no_mqtt=True))
    assert command[:3] == [sys.executable, "-m", "vision_system.apps.coordinator"]
    assert "--config" not in command
    assert "--debug" not in command
    assert "--no-mqtt" in command


def test_environment_carries_broker_selection():
    environment = build_server_environment(_options(), {"PATH": "/usr/bin"})
    assert environment["VISION_MQTT_HOST"] == "192.168.1.10"
    assert environment["VISION_MQTT_PORT"] == "1884"
    assert environment["PATH"] == "/usr/bin"


def test_process_streams_output_and_reports_exit():
    process = ServerProcess(spawn=_runs("print('coordinator_started'); raise SystemExit(3)"))
    process.start(_options())
    collected: list[str] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        collected += process.drain_logs()
        if any("code 3" in line for line in collected):
            break
        time.sleep(0.05)
    else:
        pytest.fail("the process never reported its exit code")
    assert collected[0].startswith("$ ")
    assert any("coordinator_started" in line for line in collected)
    assert process.running is False
    assert process.exit_code == 3


def test_process_refuses_double_start():
    process = ServerProcess(spawn=_runs("import time; time.sleep(30)"))
    process.start(_options())
    try:
        with pytest.raises(RuntimeError):
            process.start(_options())
    finally:
        process.stop(timeout=5.0)
    assert process.running is False


def test_process_stop_is_safe_before_start_and_after_exit():
    process = ServerProcess(spawn=_runs("import time; time.sleep(30)"))
    process.stop()                       # never started: must not raise
    process.start(_options())
    process.stop(timeout=5.0)
    assert process.running is False
