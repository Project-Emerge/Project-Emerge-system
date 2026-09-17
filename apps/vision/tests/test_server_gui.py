import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vision_system.apps.server_gui import (
    PRESENCE_TIMEOUT_NS,
    STALE_POSE_NS,
    VIEWPORT_QUANTUM_M,
    DeploymentStatus,
    ServerLaunchOptions,
    ServerProcess,
    StatusMonitor,
    WorldModel,
    build_server_command,
    build_server_environment,
    fit_viewport,
    roster_from_config,
)
from vision_system.apps.server_gui.presenters import describe_message
from vision_system.core.config import (
    AppConfig,
    ArucoConfig,
    CameraCalibration,
    CameraConfig,
    ReferenceMarkerConfig,
    save_json,
)
from vision_system.monitoring.deployment import CameraIssue
from vision_system.transport.payloads import PoseUpdate, parse


def _options(**overrides) -> ServerLaunchOptions:
    defaults = {
        "config": Path("config.local.json"),
        "calibrations": Path("calibrations"),
        "cache": Path(".state/last_good_config.json"),
        "mqtt_host": "192.168.1.10",
        "mqtt_port": 1884,
    }
    return ServerLaunchOptions(**{**defaults, **overrides})


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
    process = ServerProcess()
    command = [sys.executable, "-c", "print('coordinator_started'); raise SystemExit(3)"]
    process._spawn = lambda *args, **kwargs: subprocess.Popen(
        command, stdout=kwargs["stdout"], stderr=kwargs["stderr"], text=True
    )
    process.start(_options())
    collected: list[str] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        collected += process.drain_logs()
        if any("codice 3" in line for line in collected):
            break
        time.sleep(0.05)
    else:
        pytest.fail("il processo non ha riportato il codice di uscita")
    assert collected[0].startswith("$ ")
    assert any("coordinator_started" in line for line in collected)
    assert process.running is False
    assert process.exit_code == 3


def test_process_refuses_double_start():
    process = ServerProcess()
    process._spawn = lambda *args, **kwargs: subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=kwargs["stdout"],
        stderr=kwargs["stderr"],
        text=True,
    )
    process.start(_options())
    try:
        with pytest.raises(RuntimeError):
            process.start(_options())
    finally:
        process.stop(timeout=5.0)
    assert process.running is False


def test_process_stop_is_safe_before_start_and_after_exit():
    process = ServerProcess()
    process.stop()                       # mai avviato: nessun errore
    process._spawn = lambda *args, **kwargs: subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=kwargs["stdout"],
        stderr=kwargs["stderr"],
        text=True,
    )
    process.start(_options())
    process.stop(timeout=5.0)
    assert process.running is False
    process.stop(timeout=5.0)            # idempotente: la GUI la chiama da piu' punti
    assert process.exit_code is not None



def _apply(status, topic, body, now_ns):
    """Decode like the panel does, feed the model, return the console line if any."""
    message = parse(topic, body)
    if message is None:
        return None
    status.apply(message, now_ns)
    return describe_message(message)


def _apply_pose(world, body, now_ns):
    pose = PoseUpdate.from_body(body)
    if pose is None:
        return None
    return world.apply(pose, now_ns)


def test_status_merges_node_and_coordinator_views():
    status = DeploymentStatus(["cam_0", "cam_1"])
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 120},
        now_ns=now,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {
            "role": "coordinator",
            "poses_published": 42,
            "tracked_tags": [7],
            "cameras": {
                "cam_0": {
                    "online": True,
                    "observations_received": 118,
                    "calibrated": True,
                    "age_ms": 12.5,
                },
                "cam_1": {
                    "online": False,
                    "observations_received": 0,
                    "calibrated": False,
                    "age_ms": None,
                },
            },
        },
        now_ns=now,
    )
    rows = {row.camera_id: row for row in status.rows(now_ns=now)}
    assert status.poses_published == 42
    assert status.tracked_tags == [7]
    assert rows["cam_0"].node_online and rows["cam_0"].server_online
    assert rows["cam_0"].node_observations == 120
    assert rows["cam_0"].observations_received == 118
    assert rows["cam_0"].issues == ()
    assert rows["cam_1"].server_online is False
    assert CameraIssue.CALIBRATION_MISSING_ON_SERVER in rows["cam_1"].issues


def test_status_flags_node_publishing_without_reaching_the_server():
    status = DeploymentStatus(["cam_0"])
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 5},
        now_ns=now,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "coordinator", "poses_published": 0, "cameras": {"cam_0": {"online": False}}},
        now_ns=now,
    )
    row = status.rows(now_ns=now)[0]
    assert row.node_online is True
    assert row.server_online is False
    assert CameraIssue.NODE_UP_NO_OBSERVATIONS in row.issues


def test_status_expires_silent_processes():
    status = DeploymentStatus(["cam_0"])
    start = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 5},
        now_ns=start,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "coordinator", "cameras": {"cam_0": {"online": True}}},
        now_ns=start,
    )
    later = start + PRESENCE_TIMEOUT_NS + 1
    row = status.rows(now_ns=later)[0]
    assert row.node_online is False
    assert row.server_online is False
    assert status.coordinator_online(now_ns=later) is False


def test_status_discovers_unexpected_cameras_and_reports_drift():
    status = DeploymentStatus()
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {
            "role": "node",
            "camera_id": "cam_3",
            "observations_published": 0,
            "excluded_for_drift": True,
        },
        now_ns=now,
    )
    row = status.rows(now_ns=now)[0]
    assert row.camera_id == "cam_3"
    assert CameraIssue.DRIFT_RECALIBRATE in row.issues


def test_status_formats_events_and_status_messages():
    status = DeploymentStatus()
    now = 10 * PRESENCE_TIMEOUT_NS
    line = _apply(
        status,
        "vision/default/indoor-01/event",
        {
            "code": "MISSING_CALIBRATION",
            "message": "Cameras without calibration",
            "severity": "warning",
            "cameras": ["cam_2"],
        },
        now_ns=now,
    )
    assert line is not None and line.startswith("[WARNING] MISSING_CALIBRATION")
    assert "cam_2" in line
    assert _apply(
        status,
        "vision/default/indoor-01/status", {"online": True, "reason": "running"}, now_ns=now
    )
    assert _apply(status, "vision/default/indoor-01/pose/7", {"tag_id": 7}, now_ns=now) is None


def test_monitor_subscribes_to_the_configured_base_topic():
    class FakeClient:
        def __init__(self) -> None:
            self.subscribed: list[str] = []

        def subscribe(self, topic, qos=0) -> None:
            self.subscribed.append(topic)

    fake = FakeClient()
    monitor = StatusMonitor("vision/lab/indoor-02", client_factory=lambda client_id: fake)
    monitor._on_connect(fake, None, None, 0, None)
    assert fake.subscribed == [
        "vision/lab/indoor-02/metrics",
        "vision/lab/indoor-02/event",
        "vision/lab/indoor-02/status",
        "vision/lab/indoor-02/pose/+",
    ]
    assert monitor.connected.is_set()


def test_monitor_reports_rejected_connections():
    class FakeClient:
        def subscribe(self, topic, qos=0) -> None:
            raise AssertionError("non deve sottoscrivere dopo un rifiuto")

    fake = FakeClient()
    monitor = StatusMonitor("vision/lab/indoor-02", client_factory=lambda client_id: fake)
    monitor._on_connect(fake, None, None, 5, None)
    assert monitor.connected.is_set() is False
    assert "5" in (monitor.error or "")


def test_roster_reads_the_camera_ids_from_the_config(tmp_path):
    config_path = tmp_path / "config.local.json"
    save_json(
        config_path,
        AppConfig(cameras=[CameraConfig(id="cam_0", source=0), CameraConfig(id="cam_1", source=1)]),
    )
    config, cameras = roster_from_config(config_path, tmp_path / "missing.json")
    assert cameras == ["cam_0", "cam_1"]
    assert config.base_topic == "vision/default/indoor-01"


def _pose(tag_id: int, x: float, y: float, yaw_deg: float = 0.0, **overrides) -> dict:
    body = {
        "tag_id": tag_id,
        "position_m": {"x": x, "y": y, "z": 0.0},
        "euler_deg": {"roll": 0.0, "pitch": 0.0, "yaw": yaw_deg},
        "quality": 0.9,
        "predicted": False,
        "visible_by": ["cam_0", "cam_1"],
    }
    body.update(overrides)
    return body


def test_world_model_tracks_positions_and_trail():
    world = WorldModel(trail_seconds=2.0)
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 1.0, 2.0, yaw_deg=90.0), now_ns=start)
    _apply_pose(world, _pose(7, 1.2, 2.1), now_ns=start + 100_000_000)
    tag = world.tags()[0]
    assert (tag.tag_id, tag.x_m, tag.y_m) == (7, 1.2, 2.1)
    assert tag.cameras == ("cam_0", "cam_1")
    assert tag.stale(start + 100_000_000) is False
    assert world.trail(7) == [(1.0, 2.0), (1.2, 2.1)]


def test_world_model_drops_trail_points_older_than_the_window():
    world = WorldModel(trail_seconds=1.0)
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 0.0, 0.0), now_ns=start)
    _apply_pose(world, _pose(7, 1.0, 0.0), now_ns=start + 2_000_000_000)
    world.expire(start + 2_000_000_000)
    assert world.trail(7) == [(1.0, 0.0)]


def test_world_model_falls_back_to_the_quaternion_for_heading():
    world = WorldModel()
    body = _pose(3, 0.0, 0.0)
    del body["euler_deg"]
    body["orientation_xyzw"] = {
        "x": 0.0,
        "y": 0.0,
        "z": math.sin(math.pi / 4),
        "w": math.cos(math.pi / 4),
    }
    tag = _apply_pose(world, body, now_ns=STALE_POSE_NS)
    assert tag is not None
    assert tag.heading_rad == pytest.approx(math.pi / 2, abs=1e-6)


def test_world_model_ignores_malformed_pose_payloads():
    world = WorldModel()
    assert _apply_pose(world, {"tag_id": 1}, now_ns=STALE_POSE_NS) is None
    assert _apply_pose(world, {"position_m": {"x": 1.0, "y": 2.0}}, now_ns=STALE_POSE_NS) is None
    assert (
        _apply_pose(
            world,
            {"tag_id": "sette", "position_m": {"x": 1.0, "y": 2.0}}, now_ns=STALE_POSE_NS
        )
        is None
    )
    assert world.tags() == []


def test_world_model_marks_stale_tags_and_forgets_the_oldest():
    world = WorldModel()
    start = 10 * STALE_POSE_NS
    _apply_pose(world, _pose(7, 1.0, 1.0), now_ns=start)
    stale_moment = start + STALE_POSE_NS + 1
    assert world.tags()[0].stale(stale_moment) is True
    world.expire(start + 6 * STALE_POSE_NS)
    assert world.tags() == []


def test_world_model_scene_uses_references_and_calibrated_cameras():
    world = WorldModel()
    config = AppConfig(
        aruco=ArucoConfig(
            reference_markers=[
                ReferenceMarkerConfig(
                    id=40, size_m=0.15, position_m=(3.0, 1.0, 0.0), orientation_xyzw=(0, 0, 0, 1)
                )
            ]
        )
    )
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=0,
        image_size=(1920, 1080),
        camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
        distortion=[0.0] * 8,
        # Camera mounted at (2.0, 1.0, 2.5) looking along world -Y.
        world_from_camera=[[1, 0, 0, 2.0], [0, 0, -1, 1.0], [0, 1, 0, 2.5], [0, 0, 0, 1]],
        intrinsic_median_error_px=0.2,
        intrinsic_p95_error_px=0.4,
        captured_at="2026-09-16T00:00:00+00:00",
        opencv_version="test",
        board_checksum="test",
    )
    uncalibrated = calibration.model_copy(update={"camera_id": "cam_1", "world_from_camera": None})
    world.update_scene(config, {"cam_0": calibration, "cam_1": uncalibrated})
    assert world.references == [(40, 3.0, 1.0)]
    x_m, y_m, heading = world.cameras["cam_0"]
    assert (x_m, y_m) == (2.0, 1.0)
    assert heading == pytest.approx(-math.pi / 2)
    assert "cam_1" not in world.cameras


def test_world_bounds_contain_scene_and_tags():
    world = WorldModel()
    _apply_pose(world, _pose(7, 4.0, -1.0), now_ns=STALE_POSE_NS)
    min_x, min_y, max_x, max_y = world.bounds(margin_m=0.5)
    assert min_x <= -0.5 and min_y <= -1.5
    assert max_x >= 4.5 and max_y >= 0.5


def test_world_reset_forgets_every_tag():
    world = WorldModel()
    _apply_pose(world, _pose(7, 1.0, 1.0), now_ns=STALE_POSE_NS)
    world.reset()
    assert world.tags() == []
    assert world.trail(7) == []


def test_viewport_snaps_to_the_quantum_grid():
    assert fit_viewport(None, (-0.1, 0.2, 2.6, 1.1)) == (-0.5, 0.0, 3.0, 1.5)


def test_viewport_is_kept_while_the_content_still_fits():
    """Rescaling on every frame is what made the view flicker."""
    viewport = fit_viewport(None, (0.0, 0.0, 4.0, 3.0))
    for x_max in (3.2, 3.9, 2.5, 4.0):
        assert fit_viewport(viewport, (0.0, 0.0, x_max, 3.0)) == viewport


def test_viewport_expands_when_a_tag_leaves_it():
    viewport = fit_viewport(None, (0.0, 0.0, 4.0, 3.0))
    grown = fit_viewport(viewport, (0.0, 0.0, 4.2, 3.0))
    assert grown[2] >= 4.2
    assert grown != viewport
    assert grown[2] % VIEWPORT_QUANTUM_M == pytest.approx(0.0)


def test_viewport_shrinks_only_when_far_too_large():
    viewport = fit_viewport(None, (0.0, 0.0, 10.0, 10.0))
    assert fit_viewport(viewport, (0.0, 0.0, 6.0, 6.0)) == viewport
    assert fit_viewport(viewport, (0.0, 0.0, 2.0, 2.0)) == (0.0, 0.0, 2.0, 2.0)


@pytest.mark.parametrize("module", ["vision_system.apps.server_gui", "vision_system.gui"])
def test_importing_the_panel_does_not_pull_in_tkinter(module):
    # The whole suite runs headless only because tkinter is imported lazily, inside
    # the widget classes. A stray module-scope import would break CI on any machine
    # without python3-tk, so assert the contract instead of trusting review.
    probe = f"import {module}, sys; assert 'tkinter' not in sys.modules"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "src")
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=environment
    )
    assert result.returncode == 0, result.stderr
