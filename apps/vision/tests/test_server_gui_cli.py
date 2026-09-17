"""Argument parsing and the two environment failure paths."""

import pytest

from vision_system.apps import server_gui
from vision_system.apps.server_gui import strings
from vision_system.gui.toolkit import GuiUnavailable


def test_defaults_match_the_documented_command_line():
    options, verbose = server_gui.parse_args([])
    assert options.config is None
    assert options.mqtt_port == 1883
    assert options.debug is True            # the world view is pre-selected
    assert options.no_mqtt is False
    assert verbose is False


def test_no_debug_window_is_an_inverted_flag():
    options, _ = server_gui.parse_args(["--no-debug-window"])
    assert options.debug is False


def test_no_mqtt_reaches_the_launch_options():
    # The checkbox existed long before the flag did; both must agree now.
    options, _ = server_gui.parse_args(["--no-mqtt"])
    assert options.no_mqtt is True


def test_broker_defaults_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("VISION_MQTT_HOST", "10.0.0.5")
    monkeypatch.setenv("VISION_MQTT_PORT", "1885")
    options, _ = server_gui.parse_args([])
    assert (options.mqtt_host, options.mqtt_port) == ("10.0.0.5", 1885)


def test_explicit_flags_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("VISION_MQTT_HOST", "10.0.0.5")
    options, _ = server_gui.parse_args(["--mqtt-host", "localhost"])
    assert options.mqtt_host == "localhost"


def test_a_missing_toolkit_exits_with_a_plain_message(monkeypatch, capsys):
    def no_toolkit():
        raise GuiUnavailable(strings.TKINTER_MISSING)

    monkeypatch.setattr("vision_system.apps.server_gui.load_toolkit", no_toolkit)
    with pytest.raises(SystemExit) as failure:
        server_gui.server_gui_main([])
    # Not argparse's exit code 2 with a usage banner: this is the environment,
    # not a mistyped flag.
    assert failure.value.code == strings.TKINTER_MISSING
    assert "usage:" not in capsys.readouterr().err
