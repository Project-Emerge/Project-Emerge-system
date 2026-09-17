"""The calibration panel's package surface and command line."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vision_system.apps import calibration_gui
from vision_system.gui.toolkit import GuiUnavailable


def test_defaults_match_the_documented_layout():
    settings = calibration_gui.parse_args([])
    assert settings.config_path is None
    assert settings.calibrations_dir == Path("calibrations")
    assert settings.board_format == "a4"
    assert settings.photo_root == Path("photo")


def test_the_panel_does_not_publish_unless_asked():
    # It reads the arena's state; writing config or poses is the tools' job.
    assert calibration_gui.parse_args([]).mqtt_enabled is False
    assert calibration_gui.parse_args(["--publish-mqtt"]).mqtt_enabled is True


def test_the_board_format_reaches_the_settings():
    assert calibration_gui.parse_args(["--board-format", "a3"]).board_format == "a3"


def test_an_unsupported_board_format_is_rejected():
    with pytest.raises(SystemExit):
        calibration_gui.parse_args(["--board-format", "a5"])


def test_paths_are_taken_as_given():
    settings = calibration_gui.parse_args(
        ["--config", "c.json", "--calibrations", "calib", "--photo-root", "shots"]
    )
    assert settings.config_path == Path("c.json")
    assert settings.calibrations_dir == Path("calib")
    assert settings.photo_root == Path("shots")


def test_a_missing_toolkit_exits_with_a_plain_message(monkeypatch, capsys):
    def no_toolkit():
        raise GuiUnavailable("tkinter is not available: install python3-tk")

    monkeypatch.setattr("vision_system.apps.calibration_gui.load_toolkit", no_toolkit)
    with pytest.raises(SystemExit) as failure:
        calibration_gui.calibration_gui_main([])
    assert "python3-tk" in str(failure.value.code)
    assert "usage:" not in capsys.readouterr().err


def test_importing_the_panel_does_not_pull_in_tkinter():
    # Same contract as the server panel: the view is imported only when a window
    # is actually opened, so the package stays usable on a headless machine.
    probe = (
        "import vision_system.apps.calibration_gui, sys;"
        " assert 'tkinter' not in sys.modules"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=environment
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "name",
    [
        "CALIBRATION_STEPS",
        "CalibrationController",
        "CalibrationOverview",
        "GuiSettings",
        "calibration_gui_main",
        "describe_calibrations",
        "parse_args",
    ],
)
def test_the_package_exposes_its_public_names(name):
    assert hasattr(calibration_gui, name)
    assert name in calibration_gui.__all__
