"""Step 1 of the panel: choosing the number of webcams and this PC's share.

The scenario throughout is the four-camera arena split 1+2+1 over three PCs, run
from the client that owns cam_1 and cam_2.
"""

from pathlib import Path

import pytest

from vision_system.apps.calibration_gui import jobs, steps
from vision_system.apps.calibration_gui.controller import CalibrationController
from vision_system.apps.calibration_gui.settings import GuiSettings
from vision_system.apps.calibration_gui.status import (
    CalibrationOverview,
    CameraStatus,
    describe_calibrations,
)
from vision_system.apps.calibration_gui.view.shell import (
    apply_setup_fields,
    read_setup_fields,
    read_step_fields,
)
from vision_system.core.config import AppConfig, save_json
from vision_system.core.setup import DeploymentSetup, load_setup
from vision_system.gui.tasks import TaskEventKind, TaskRunner

ROSTER = ("cam_0", "cam_1", "cam_2", "cam_3")
LOCAL = ("cam_1", "cam_2")


def _status(camera_id, *, intrinsics=True, extrinsics=True):
    return CameraStatus(
        camera_id=camera_id,
        source=0,
        has_intrinsics=intrinsics,
        intrinsic_quality_passed=True if intrinsics else None,
        has_extrinsics=extrinsics,
        extrinsic_quality_passed=True if extrinsics else None,
    )


def _overview(*cameras, local=LOCAL, references=9):
    return CalibrationOverview(
        cameras=cameras,
        calibrations_dir=Path("calibrations"),
        config_path=Path("c.json"),
        reference_marker_count=references,
        local_camera_ids=local,
    )


def _run(spec):
    """Drive a TaskSpec to completion on the calling thread, collecting its lines."""
    runner = TaskRunner(executor=lambda run: run())
    runner.submit(spec)
    events = runner.drain()
    failures = [e.message for e in events if e.kind is TaskEventKind.FAILED]
    assert not failures, failures
    lines = [e.message for e in events if e.kind is TaskEventKind.LOG]
    payload = next(e.payload for e in events if e.kind is TaskEventKind.FINISHED)
    return payload, lines


def _context(settings, overview):
    return steps.ActionContext(settings=settings, overview=overview)


# ------------------------------------------------------------------ scoping ---
def test_the_roster_table_shows_the_whole_deployment_local_and_remote():
    from vision_system.apps.calibration_gui import presentation

    overview = _overview(*(_status(camera_id) for camera_id in ROSTER))
    rows = presentation.table_rows("roster", overview)

    assert [row[0] for row in rows] == list(ROSTER)
    assert [row[1] for row in rows] == [
        presentation.WHERE_REMOTE,
        presentation.WHERE_LOCAL,
        presentation.WHERE_LOCAL,
        presentation.WHERE_REMOTE,
    ]


def test_the_calibration_tables_show_only_what_this_pc_can_calibrate():
    from vision_system.apps.calibration_gui import presentation

    overview = _overview(*(_status(camera_id) for camera_id in ROSTER))
    rows = presentation.table_rows("intrinsics", overview)
    assert [row[0] for row in rows] == list(LOCAL)


def test_a_camera_on_another_pc_does_not_block_the_local_wizards():
    """Otherwise no PC in a distributed arena could ever reach step 6."""
    overview = _overview(
        _status("cam_0", intrinsics=False),
        _status("cam_1"),
        _status("cam_2"),
        _status("cam_3", intrinsics=False),
    )
    assert steps.step("extrinsics").precondition(overview).ready is True
    # The server still needs every camera placed before it can fuse.
    assert steps.step("runtime").precondition(overview).ready is False


def test_a_pc_with_no_camera_of_its_own_is_told_so_rather_than_shown_a_pass():
    overview = _overview(*(_status(c) for c in ROSTER), local=())
    readiness = steps.step("extrinsics").precondition(overview)
    assert readiness.ready is False
    assert "attached to this PC" in readiness.reason


def test_a_per_camera_action_expands_over_the_local_share_only():
    overview = _overview(*(_status(c) for c in ROSTER))
    action = next(a for a in steps.step("intrinsics").actions if a.per_camera)
    contexts = steps.expand(action, _context(GuiSettings(), overview))
    assert [context.camera_id for context in contexts] == list(LOCAL)


def test_selecting_sources_never_trims_the_roster_to_the_local_share():
    overview = _overview(*(_status(c) for c in ROSTER))
    action = next(a for a in steps.step("cameras").actions if a.id == "select")
    argv = list(action.build(_context(GuiSettings(config_path=Path("c.json")), overview)).argv)
    assert "--cameras" not in argv
    assert argv[argv.index("--local-cameras") + 1 :][:2] == list(LOCAL)


def test_the_server_panel_opens_before_anything_is_calibrated():
    """When nothing works yet, the roster panel is what says why."""
    nothing_done = _overview(*(_status(c, intrinsics=False, extrinsics=False) for c in ROSTER))
    step = steps.step("deployment")
    action = next(a for a in step.actions if a.id == "server_panel")
    assert step.precondition(nothing_done).ready is True
    assert action.enabled(nothing_done) is True


# --------------------------------------------------------------------- save ---
def test_saving_the_step_resizes_the_roster_and_records_the_local_share(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig())
    settings = GuiSettings(
        config_path=config_path,
        roster=("4",),
        setup=DeploymentSetup(
            mode="distributed", local_camera_ids=list(LOCAL), mqtt_host="192.168.1.10"
        ),
    )

    result, lines = _run(jobs.save_setup_job(_context(settings, _overview())))

    assert result.camera_ids == ROSTER
    assert result.local_camera_ids == LOCAL
    assert load_setup(config_path).mqtt_host == "192.168.1.10"
    assert any("make client CAMERA=cam_1" in line for line in lines)


def test_reducing_the_count_keeps_the_calibration_files_on_disk(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig())
    calibrations = tmp_path / "calibrations"
    calibrations.mkdir()
    artifact = calibrations / "cam_3.json"
    artifact.write_text("{}")
    settings = GuiSettings(
        config_path=config_path, calibrations_dir=calibrations, roster=("2",)
    )

    result, _ = _run(jobs.save_setup_job(_context(settings, _overview())))

    assert result.camera_ids == ("cam_0", "cam_1")
    assert artifact.exists(), "a camera leaving the roster must not delete its calibration"


def test_dropping_a_camera_is_naming_the_ones_that_stay(tmp_path):
    """No separate removal list: the roster field says what the arena has."""
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig())
    settings = GuiSettings(config_path=config_path, roster=("cam_0", "cam_2", "cam_3"))

    result, _ = _run(jobs.save_setup_job(_context(settings, _overview())))

    assert result.camera_ids == ("cam_0", "cam_2", "cam_3")
    # The field now shows ids, so saving again is a no-op rather than a second cut.
    assert settings.roster == ("cam_0", "cam_2", "cam_3")
    again, _ = _run(jobs.save_setup_job(_context(settings, _overview())))
    assert again.camera_ids == ("cam_0", "cam_2", "cam_3")


def test_a_count_typed_in_the_field_comes_back_as_the_names_it_produced(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig())
    settings = GuiSettings(config_path=config_path, roster=("2",))

    _run(jobs.save_setup_job(_context(settings, _overview())))

    assert settings.roster == ("cam_0", "cam_1")


def test_saving_without_a_configuration_file_says_so(tmp_path):
    runner = TaskRunner(executor=lambda run: run())
    runner.submit(jobs.save_setup_job(_context(GuiSettings(), _overview())))
    failures = [e for e in runner.drain() if e.kind is TaskEventKind.FAILED]
    assert failures and "configuration file" in failures[0].message


# -------------------------------------------------------------------- form ---
def test_the_form_round_trips_what_is_already_saved():
    settings = GuiSettings(
        roster=("cam_0", "cam_1", "cam_2"),
        setup=DeploymentSetup(
            mode="distributed",
            local_camera_ids=list(LOCAL),
            mqtt_host="192.168.1.10",
            mqtt_port=1884,
        ),
    )
    assert read_setup_fields(settings) == {
        "deployment_mode": "distributed",
        "roster": "cam_0 cam_1 cam_2",
        "local_cameras": "cam_1 cam_2",
        "mqtt_host": "192.168.1.10",
        "mqtt_port": "1884",
    }


@pytest.mark.parametrize("typed", ["cam_1 cam_2", "cam_1,cam_2", " cam_1 , cam_2 "])
def test_the_local_selection_accepts_the_separators_an_operator_reaches_for(typed):
    settings = GuiSettings()
    apply_setup_fields(
        settings,
        {"deployment_mode": "distributed", "local_cameras": typed, "roster": "4"},
    )
    assert settings.setup.local_camera_ids == list(LOCAL)


def test_switching_back_to_a_single_pc_drops_a_stale_local_selection():
    settings = GuiSettings(
        setup=DeploymentSetup(mode="distributed", local_camera_ids=list(LOCAL))
    )
    apply_setup_fields(settings, {"deployment_mode": "single-pc", "local_cameras": "cam_1"})
    assert settings.setup.local_camera_ids == []
    assert settings.setup.local_ids(AppConfig()) == list(ROSTER)


def test_a_half_typed_port_leaves_the_saved_one_alone():
    settings = GuiSettings(setup=DeploymentSetup(mqtt_port=1884))
    apply_setup_fields(settings, {"mqtt_port": "18", "mqtt_host": ""})
    assert settings.setup.mqtt_port == 18
    apply_setup_fields(settings, {"mqtt_port": "", "mqtt_host": ""})
    assert settings.setup.mqtt_port == 18


# -------------------------------------------------------------- controller ---
def test_the_controller_adopts_the_setup_saved_beside_the_configuration(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(config_path, AppConfig())
    setup = DeploymentSetup(mode="distributed", local_camera_ids=list(LOCAL))
    save_json(config_path.with_suffix(".setup.json"), setup)
    settings = GuiSettings()
    controller = CalibrationController(
        settings,
        tasks=TaskRunner(executor=lambda run: run()),
        overview_loader=lambda s: describe_calibrations(
            AppConfig(),
            {},
            calibrations_dir=Path("calibrations"),
            local_camera_ids=s.local_camera_ids(AppConfig()),
        ),
    )

    controller.set_config_path(config_path)

    assert settings.setup == setup
    assert controller.overview.local().camera_ids() == LOCAL
    assert controller.overview.remote_camera_ids() == ("cam_0", "cam_3")


def test_a_local_selection_left_over_from_a_bigger_roster_does_not_break_the_panel():
    """Mid-edit the two documents can disagree; the panel must still paint."""
    settings = GuiSettings(
        setup=DeploymentSetup(mode="distributed", local_camera_ids=["cam_3"])
    )
    two_cameras = AppConfig(cameras=AppConfig().cameras[:2])
    assert settings.local_camera_ids(two_cameras) == ("cam_0", "cam_1")


def test_a_rebuilt_form_is_seeded_with_what_the_settings_already_hold():
    # Regression: only step 1 was seeded, so revisiting another step showed empty
    # boxes and the next button press wrote those blanks back over good values.
    settings = GuiSettings(photo_root=Path("shots"), cameras=("cam_1", "cam_2"))
    values = read_step_fields(settings)
    assert values["photo_root"] == "shots"
    assert values["cameras"] == "cam_1 cam_2"
    assert values["reference_markers"] == ""
    assert values["allow_low_quality"] is False
    assert values["deployment_mode"] == "single-pc"


def test_the_step_cuts_a_new_roster_from_the_example_configuration(tmp_path):
    """What the operator sees in the console when step 1 creates a configuration."""
    example = Path(__file__).resolve().parents[1] / "config.example.json"
    settings = GuiSettings(
        config_path=tmp_path / "config.local.json", roster=("2",), template_path=example
    )

    result, lines = _run(jobs.save_setup_job(_context(settings, _overview())))

    assert result.camera_ids == ("cam_0", "cam_1")
    assert any(str(example) in line for line in lines)
    from vision_system.core.config import load_config

    written = load_config(tmp_path / "config.local.json")
    assert [c.source for c in written.cameras] == [5, 1]
    assert written.site == "lab"


def test_an_example_that_is_not_there_is_reported_not_fatal(tmp_path):
    settings = GuiSettings(
        config_path=tmp_path / "config.local.json",
        roster=("1",),
        template_path=Path("nowhere/config.example.json"),
    )

    result, lines = _run(jobs.save_setup_job(_context(settings, _overview())))

    assert result.camera_ids == ("cam_0",)
    assert any("not found" in line for line in lines)
