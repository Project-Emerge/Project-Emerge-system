"""The workflow registry: gates, per-camera expansion and the action wiring."""

from pathlib import Path

import pytest

from vision_system.apps.calibration_gui import steps
from vision_system.apps.calibration_gui.settings import GuiSettings
from vision_system.apps.calibration_gui.status import (
    MIN_REFERENCES_FOR_EXTRINSICS,
    CalibrationOverview,
    CameraStatus,
)
from vision_system.gui.process import CommandSpec
from vision_system.gui.tasks import TaskSpec

CALIBRATIONS = Path("calibrations")


def _status(camera_id: str, *, intrinsics=True, extrinsics=False, stale=False) -> CameraStatus:
    return CameraStatus(
        camera_id=camera_id,
        source=0,
        has_intrinsics=intrinsics,
        intrinsic_quality_passed=True if intrinsics else None,
        has_extrinsics=extrinsics,
        extrinsic_quality_passed=True if extrinsics else None,
        geometry_error="digital zoom differs" if stale else None,
    )


def _overview(*cameras: CameraStatus, references: int = 0, config=Path("c.json")):
    return CalibrationOverview(
        cameras=cameras,
        calibrations_dir=CALIBRATIONS,
        config_path=config,
        reference_marker_count=references,
    )


def _context(overview, camera_id=None):
    return steps.ActionContext(
        settings=GuiSettings(config_path=Path("c.json")),
        overview=overview,
        camera_id=camera_id,
    )


def test_the_workflow_is_ordered_and_uniquely_identified():
    ids = [step.id for step in steps.CALIBRATION_STEPS]
    assert ids == sorted(set(ids), key=ids.index), "duplicate step id"
    assert [step.index for step in steps.CALIBRATION_STEPS] == [1, 2, 3, 4, 5, 6]


def test_every_action_declares_a_kind_the_controller_can_run():
    for step in steps.CALIBRATION_STEPS:
        for action in step.actions:
            assert action.kind in ("process", "task"), f"{step.id}.{action.id}"


def test_choosing_sources_and_the_board_need_nothing_in_place():
    empty = _overview(config=None)
    assert steps.step("cameras").precondition(empty).ready is True
    assert steps.step("board").precondition(empty).ready is True


def test_intrinsics_waits_for_a_configuration_file():
    blocked = steps.step("intrinsics").precondition(_overview(config=None))
    assert blocked.ready is False
    assert "configuration" in blocked.reason


def test_extrinsics_names_the_cameras_that_still_lack_intrinsics():
    overview = _overview(_status("cam_0"), _status("cam_1", intrinsics=False))
    blocked = steps.step("extrinsics").precondition(overview)
    assert blocked.ready is False
    assert "cam_1" in blocked.reason and "cam_0" not in blocked.reason


def test_a_stale_camera_is_not_reported_as_a_missing_one():
    # "Intrinsics are missing" would send the operator looking for a file that is
    # right there; the artifact exists, the camera settings moved under it.
    overview = _overview(_status("cam_0", stale=True), references=9)
    blocked = steps.step("extrinsics").precondition(overview)
    assert blocked.ready is False
    assert "missing" not in blocked.reason
    assert "settings changed" in blocked.reason and "cam_0" in blocked.reason


def test_a_poor_fit_is_reported_as_a_quality_problem():
    poor = CameraStatus(
        camera_id="cam_0", source=0, has_intrinsics=True, intrinsic_quality_passed=False
    )
    blocked = steps.step("extrinsics").precondition(_overview(poor, references=9))
    assert "quality gates" in blocked.reason


def test_extrinsics_requires_enough_reference_markers():
    overview = _overview(_status("cam_0"), references=MIN_REFERENCES_FOR_EXTRINSICS - 1)
    blocked = steps.step("extrinsics").precondition(overview)
    assert blocked.ready is False
    assert "reference markers" in blocked.reason
    ready = _overview(_status("cam_0"), references=MIN_REFERENCES_FOR_EXTRINSICS)
    assert steps.step("extrinsics").precondition(ready).ready is True


def test_runtime_waits_for_every_camera_to_be_placed():
    partial = _overview(_status("cam_0", extrinsics=True), _status("cam_1"))
    assert steps.step("runtime").precondition(partial).ready is False
    complete = _overview(_status("cam_0", extrinsics=True), _status("cam_1", extrinsics=True))
    assert steps.step("runtime").precondition(complete).ready is True


@pytest.mark.parametrize(
    "cameras",
    [
        ("cam_0", "cam_1"),
        ("cam_0", "cam_1", "cam_2"),
        ("cam_0", "cam_1", "cam_2", "cam_3"),
    ],
)
def test_a_per_camera_action_covers_exactly_the_configured_roster(cameras):
    overview = _overview(*(_status(name) for name in cameras))
    action = next(a for a in steps.step("intrinsics").actions if a.id == "wizard")
    contexts = steps.expand(action, _context(overview))
    assert [context.camera_id for context in contexts] == list(cameras)
    built = [action.build(context) for context in contexts]
    assert all(isinstance(spec, CommandSpec) for spec in built)
    assert [spec.camera_id for spec in built] == list(cameras)


def test_a_per_camera_action_can_be_narrowed_to_one_camera():
    overview = _overview(_status("cam_0"), _status("cam_1"), _status("cam_2"))
    action = next(a for a in steps.step("intrinsics").actions if a.id == "wizard")
    contexts = steps.expand(action, _context(overview), cameras=("cam_1",))
    assert [context.camera_id for context in contexts] == ["cam_1"]


def test_a_whole_roster_action_runs_once():
    overview = _overview(_status("cam_0"), _status("cam_1"))
    action = next(a for a in steps.step("cameras").actions if a.id == "select")
    assert len(steps.expand(action, _context(overview))) == 1


def test_task_actions_build_task_specs_and_process_actions_build_commands():
    overview = _overview(_status("cam_0"))
    for step in steps.CALIBRATION_STEPS:
        for action in step.actions:
            for context in steps.expand(action, _context(overview)):
                built = action.build(context)
                expected = TaskSpec if action.kind == "task" else CommandSpec
                assert isinstance(built, expected), f"{step.id}.{action.id}"


def test_the_extrinsics_button_is_disabled_with_a_reason():
    action = next(a for a in steps.step("extrinsics").actions if a.id == "wizard")
    blocked = _overview(_status("cam_0", intrinsics=False))
    assert action.enabled(blocked) is False
    assert action.disabled_hint
    assert action.enabled(_overview(_status("cam_0"))) is True


def test_runtime_does_not_call_a_stale_camera_a_missing_one():
    # Regression: a camera with extrinsics whose settings later changed was
    # reported as "Extrinsics are missing", which is the wrong thing to fix.
    overview = _overview(_status("cam_0", extrinsics=True, stale=True))
    blocked = steps.step("runtime").precondition(overview)
    assert blocked.ready is False
    assert "missing" not in blocked.reason
    assert "settings changed" in blocked.reason


def test_runtime_names_a_camera_that_was_never_placed():
    overview = _overview(_status("cam_0", extrinsics=True), _status("cam_1"))
    blocked = steps.step("runtime").precondition(overview)
    assert "Extrinsics are missing for: cam_1" in blocked.reason
