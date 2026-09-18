"""The calibration panel's package surface and command line."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from vision_system.apps import calibration_gui
from vision_system.core.config import load_config
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


def test_the_roster_flag_is_optional_and_takes_ids_or_a_count():
    assert calibration_gui.parse_args([]).roster == ()
    assert calibration_gui.parse_args(["--cameras", "3"]).roster == ("3",)
    assert calibration_gui.parse_args(
        ["--cameras", "cam_2", "cam_3"]
    ).roster == ("cam_2", "cam_3")


def test_an_impossible_roster_is_refused_with_a_message_not_a_traceback(tmp_path):
    settings = calibration_gui.parse_args(
        ["--config", str(tmp_path / "c.json"), "--cameras", "5"]
    )
    with pytest.raises(SystemExit, match="1 to 4 cameras"):
        calibration_gui.force_roster(settings)


def test_forcing_a_named_roster_writes_exactly_those_cameras(tmp_path):
    """--cameras cam_2 cam_3 is the command line spelling of step 1's field."""
    config_path = tmp_path / "config.local.json"
    calibration_gui.force_roster(
        calibration_gui.parse_args(["--config", str(config_path), "--cameras", "4"])
    )
    sources = {c.id: c.source for c in load_config(config_path).cameras}

    calibration_gui.force_roster(
        calibration_gui.parse_args(
            ["--config", str(config_path), "--cameras", "cam_2", "cam_3"]
        )
    )

    kept = load_config(config_path)
    assert [camera.id for camera in kept.cameras] == ["cam_2", "cam_3"]
    assert [camera.source for camera in kept.cameras] == [sources["cam_2"], sources["cam_3"]]


def test_forcing_the_roster_rewrites_the_configuration(tmp_path):
    config_path = tmp_path / "config.local.json"
    settings = calibration_gui.parse_args(["--config", str(config_path), "--cameras", "3"])
    calibration_gui.force_roster(settings)
    config = load_config(config_path)
    assert [camera.id for camera in config.cameras] == ["cam_0", "cam_1", "cam_2"]
    # Shrinking is a --force, not a merge: the tail goes, the survivors keep
    # their sources so their calibration stays valid.
    sources = [camera.source for camera in config.cameras[:2]]
    calibration_gui.force_roster(calibration_gui.parse_args(
        ["--config", str(config_path), "--cameras", "2"]
    ))
    shrunk = load_config(config_path)
    assert [camera.id for camera in shrunk.cameras] == ["cam_0", "cam_1"]
    assert [camera.source for camera in shrunk.cameras] == sources


def test_forcing_the_roster_without_a_configuration_is_refused():
    with pytest.raises(SystemExit):
        calibration_gui.force_roster(calibration_gui.parse_args(["--cameras", "2"]))


def test_forcing_a_roster_from_the_command_line_also_cuts_it_from_the_example(tmp_path):
    """--cameras is step 1 without the clicking, base configuration included."""
    from vision_system.apps.calibration_gui import force_roster, parse_args
    from vision_system.core.config import load_config

    config_path = tmp_path / "config.local.json"
    example = Path(__file__).resolve().parents[1] / "config.example.json"
    settings = parse_args(
        ["--config", str(config_path), "--cameras", "3", "--base", str(example)]
    )

    force_roster(settings)

    written = load_config(config_path)
    assert [(c.id, c.source) for c in written.cameras] == [
        ("cam_0", 5),
        ("cam_1", 1),
        ("cam_2", 2),
    ]
    assert written.site == "lab"
