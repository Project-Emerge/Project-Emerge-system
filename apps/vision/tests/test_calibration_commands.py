"""The command lines the panel launches, checked against the real CLIs."""

import sys
from pathlib import Path

import pytest

from vision_system.apps.calibration_gui import commands
from vision_system.apps.calibration_gui.settings import GuiSettings


def _settings(**overrides) -> GuiSettings:
    payload = dict(
        config_path=Path("config.local.json"),
        calibrations_dir=Path("calibrations"),
        board_format="a3",
    )
    payload.update(overrides)
    return GuiSettings(**payload)


def test_console_script_falls_back_to_module_execution(monkeypatch):
    monkeypatch.setattr(commands.shutil, "which", lambda name: None)
    assert commands.console_script("vision-calibrate", "pkg.mod") == [
        sys.executable,
        "-m",
        "pkg.mod",
    ]


def test_the_panel_does_not_open_the_broker_unless_asked():
    assert "--no-mqtt" in commands.build_probe_command(_settings()).argv
    assert "--no-mqtt" not in commands.build_probe_command(_settings(mqtt_enabled=True)).argv


def test_intrinsics_targets_one_camera_with_the_selected_board():
    spec = commands.build_intrinsics_command(_settings(), "cam_2")
    argv = list(spec.argv)
    assert argv[argv.index("--camera") + 1] == "cam_2"
    assert argv[argv.index("--board-format") + 1] == "a3"
    assert spec.camera_id == "cam_2", "the camera lock needs to know which device this takes"


def test_extrinsics_passes_the_reference_file_and_the_quality_override():
    plain = commands.build_extrinsics_command(_settings(), "cam_0").argv
    assert "--reference-markers" not in plain
    assert "--allow-low-quality" not in plain
    rich = commands.build_extrinsics_command(
        _settings(reference_markers_path=Path("refs.json"), allow_low_quality=True), "cam_0"
    ).argv
    assert "refs.json" in rich
    assert "--allow-low-quality" in rich


def test_board_generation_uses_the_selected_format_and_output():
    argv = list(commands.build_board_command(_settings(board_output=Path("assets"))).argv)
    assert argv[argv.index("--format") + 1] == "a3"
    assert argv[argv.index("--output") + 1] == "assets"


def test_an_unsupported_reference_map_mode_is_rejected_early():
    with pytest.raises(ValueError, match="unsupported reference map mode"):
        commands.build_reference_map_command(_settings(), mode="single")


def test_camera_owning_commands_declare_it():
    # Two jobs must never fight over a V4L2 node, so every command that opens one
    # has to say so.
    settings = _settings()
    for spec in (
        commands.build_intrinsics_command(settings, "cam_0"),
        commands.build_extrinsics_command(settings, "cam_0"),
        commands.build_select_cameras_command(settings),
        commands.build_configure_cameras_command(settings),
        commands.build_reference_map_command(settings),
        commands.build_reference_stitch_command(settings),
        commands.build_origin_command(settings),
        commands.build_localizer_command(settings),
    ):
        assert spec.camera_id is not None, spec.title


# Parsing each generated command with the CLI that will receive it is what catches
# a flag that was renamed or never existed -- as --mode single did. The parser is
# driven in process and interrupted the moment it succeeds, so no tool does work.
MAIN_FUNCTIONS = {
    "vision-calibrate": ("vision_system.apps.calibrate_cli", "calibration_main"),
    "vision-select-cameras": ("vision_system.apps.camera_selector", "camera_selector_main"),
    "vision-configure-cameras": (
        "vision_system.apps.camera_configurator",
        "camera_configurator_main",
    ),
    "vision-reference-map": ("vision_system.mapping.planar", "reference_mapper_main"),
    "vision-reference-stitch": ("vision_system.mapping.stitching", "reference_stitcher_main"),
    "vision-select-origin": ("vision_system.mapping.origin", "origin_selector_main"),
    "vision-localizer": ("vision_system.apps.calibrate_cli", "runtime_main"),
    "vision-server-gui": ("vision_system.apps.server_gui", "server_gui_main"),
}


class _Parsed(Exception):
    """Raised as soon as the CLI has accepted the arguments."""


def _specs(settings):
    return {
        spec.title: spec
        for spec in (
            commands.build_board_command(settings),
            commands.build_probe_command(settings),
            commands.build_intrinsics_command(settings, "cam_0"),
            commands.build_extrinsics_command(settings, "cam_0"),
            commands.build_select_cameras_command(settings, camera_ids=("cam_0", "cam_1")),
            commands.build_configure_cameras_command(settings),
            commands.build_reference_map_command(settings, mode="anchors"),
            commands.build_reference_stitch_command(settings),
            commands.build_origin_command(settings),
            commands.build_localizer_command(settings),
            commands.build_server_gui_command(settings),
        )
    }


SETTINGS = GuiSettings(
    config_path=Path("config.local.json"),
    board_format="a3",
    reference_markers_path=Path("refs.json"),
    allow_low_quality=True,
)
SPECS = _specs(SETTINGS)


def _parse(spec, monkeypatch) -> None:
    """Feed a CommandSpec to the parser of the tool it names. Raises on rejection."""
    import argparse
    import importlib

    script = Path(spec.argv[0]).name
    module_name, function_name = MAIN_FUNCTIONS[script]
    main = getattr(importlib.import_module(module_name), function_name)

    real = argparse.ArgumentParser.parse_args

    def parse_then_stop(self, args=None, namespace=None):
        real(self, args, namespace)
        raise _Parsed

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse_then_stop)
    monkeypatch.setattr(sys, "argv", ["x", *spec.argv[1:]])
    with pytest.raises(_Parsed):
        main()


@pytest.mark.parametrize("title", sorted(SPECS))
def test_every_command_is_accepted_by_the_cli_it_targets(title, monkeypatch):
    _parse(SPECS[title], monkeypatch)


def test_the_parser_check_actually_rejects_a_bad_flag(monkeypatch):
    # Guards the test above: without this, a probe that silently runs nothing
    # would make every command look valid.
    from vision_system.gui.process import CommandSpec

    broken = CommandSpec(
        argv=(
            "vision-reference-map",
            "--config",
            "config.local.json",
            "--mode",
            "single",
            "--camera",
            "all",
        ),
        title="broken",
    )
    with pytest.raises(SystemExit):
        _parse(broken, monkeypatch)
