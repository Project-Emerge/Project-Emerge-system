"""The panel's behaviour, exercised with no display and no broker."""

import threading
from pathlib import Path

import pytest

from vision_system.apps.server_gui.controller import ServerPanelController, options_from_fields
from vision_system.apps.server_gui.models import LauncherFields, NoticeCode
from vision_system.apps.server_gui.supervisor import ServerLaunchOptions
from vision_system.core.config import AppConfig, CameraConfig
from vision_system.monitoring.deployment import PRESENCE_TIMEOUT_NS
from vision_system.transport.mqtt import MqttSettings


class FakeClock:
    def __init__(self, now_ns: int = 10 * PRESENCE_TIMEOUT_NS) -> None:
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, delta_ns: int) -> None:
        self.now_ns += delta_ns


class FakeProcess:
    def __init__(self) -> None:
        self.running = False
        self.pid = None
        self.exit_code = None
        self.started: list[ServerLaunchOptions] = []
        self.stopped = 0
        self.lines: list[str] = []
        self.start_error: Exception | None = None

    def start(self, options, cwd=None):
        if self.start_error is not None:
            raise self.start_error
        self.started.append(options)
        self.running = True
        self.pid = 4242
        return ["vision-server"]

    def drain_logs(self, limit=2000):
        lines, self.lines = self.lines, []
        return lines

    def stop(self, timeout=10.0):
        self.stopped += 1
        self.running = False


class FakeMonitor:
    def __init__(self, base_topic: str, settings: MqttSettings) -> None:
        self.base_topic = base_topic
        self.settings = settings
        self.connected = threading.Event()
        self.error = None
        self.started = 0
        self.stopped = 0
        self.queued: list[tuple[str, dict]] = []

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def drain(self, limit=500):
        queued, self.queued = self.queued, []
        return queued


class FakeStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.calibrations: dict = {}
        self.changed = False

    def reload_if_changed(self) -> bool:
        changed, self.changed = self.changed, False
        return changed


class InlineExecutor:
    """Runs submitted work immediately, so off-thread calls stay deterministic."""

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, function, *args, **kwargs):
        self.submitted += 1
        function(*args, **kwargs)

    def shutdown(self, wait=True) -> None:
        pass


def _config(*camera_ids: str) -> AppConfig:
    return AppConfig(cameras=[CameraConfig(id=name, source=index)
                              for index, name in enumerate(camera_ids)])


def _controller(*camera_ids, **overrides):
    config = _config(*camera_ids)
    monitors: list[FakeMonitor] = []

    def monitor_factory(base_topic, settings):
        monitor = FakeMonitor(base_topic, settings)
        monitors.append(monitor)
        return monitor

    defaults = dict(
        process=FakeProcess(),
        monitor_factory=monitor_factory,
        store_factory=FakeStore,
        roster_loader=lambda config_path, cache_path: (config, list(camera_ids)),
        settings_factory=lambda: MqttSettings(host="env", port=1),
        clock=FakeClock(),
        executor=InlineExecutor(),
    )
    defaults.update(overrides)
    controller = ServerPanelController(
        ServerLaunchOptions(config=Path("config.json")), camera_ids, **defaults
    )
    controller.monitors = monitors
    return controller


def _codes(notices):
    return [notice.code for notice in notices]


def test_invalid_port_falls_back_and_reports_it():
    fallback = ServerLaunchOptions(mqtt_port=1883)
    options, notices = options_from_fields(LauncherFields(port="not-a-port"), fallback)
    assert options.mqtt_port == 1883
    assert _codes(notices) == [NoticeCode.INVALID_PORT]


def test_launcher_fields_become_paths_and_defaults():
    options, notices = options_from_fields(
        LauncherFields(config="  ", calibrations="calib", cache="c.json", host=" ", port="1884"),
        ServerLaunchOptions(),
    )
    assert notices == []
    assert options.config is None            # blank means "no config file"
    assert options.calibrations == Path("calib")
    assert options.mqtt_host == "localhost"
    assert options.mqtt_port == 1884


def test_connect_reports_an_unreadable_config_without_raising():
    def explode(config_path, cache_path):
        raise OSError("no such file")

    controller = _controller("cam_0", roster_loader=explode)
    notices = controller.connect(controller.options)
    assert _codes(notices) == [NoticeCode.CONFIG_UNREADABLE]
    assert controller.monitor is None


def test_connect_warns_when_the_built_in_four_camera_roster_is_used():
    # No --config and no cache: AppConfig() invents cam_0..cam_3, which on a
    # two-camera rig would otherwise be drawn as four silent phantom rows.
    controller = _controller("cam_0", "cam_1", "cam_2", "cam_3")
    options = ServerLaunchOptions(config=None, cache=Path("does-not-exist.json"))
    assert NoticeCode.DEFAULT_ROSTER in _codes(controller.connect(options))


def test_connect_is_quiet_when_a_config_file_was_given():
    controller = _controller("cam_0", "cam_1")
    codes = _codes(controller.connect(ServerLaunchOptions(config=Path("config.json"))))
    assert NoticeCode.DEFAULT_ROSTER not in codes
    assert NoticeCode.LISTENING in codes


def test_starting_a_second_coordinator_is_flagged():
    controller = _controller("cam_0")
    controller.connect(controller.options)
    controller.status.coordinator_seen_ns = controller._clock()
    codes = _codes(controller.start_server(controller.options))
    assert NoticeCode.SECOND_COORDINATOR in codes
    assert controller.process.started


def test_start_failure_is_reported_and_does_not_reconnect():
    process = FakeProcess()
    process.start_error = OSError("vision-server not found")
    controller = _controller("cam_0", process=process)
    controller.connect(controller.options)
    before = len(controller.monitors)
    assert _codes(controller.start_server(controller.options)) == [NoticeCode.START_FAILED]
    assert len(controller.monitors) == before


def test_start_only_reconnects_when_the_broker_changed():
    controller = _controller("cam_0")
    controller.connect(ServerLaunchOptions(config=Path("c.json"), mqtt_host="a", mqtt_port=1))
    assert len(controller.monitors) == 1
    controller.start_server(ServerLaunchOptions(config=Path("c.json"), mqtt_host="a", mqtt_port=1))
    assert len(controller.monitors) == 1, "same broker must not reset the roster view"
    controller.process.running = False
    controller.start_server(ServerLaunchOptions(config=Path("c.json"), mqtt_host="b", mqtt_port=1))
    assert len(controller.monitors) == 2


def test_stop_runs_off_the_calling_thread():
    executor = InlineExecutor()
    controller = _controller("cam_0", executor=executor)
    controller.process.running = True
    assert _codes(controller.stop_server()) == [NoticeCode.STOPPING]
    assert executor.submitted == 1, "stop must not block the UI thread"
    assert controller.process.stopped == 1


def test_stop_does_nothing_when_the_server_is_not_running():
    controller = _controller("cam_0")
    assert controller.stop_server() == []
    assert controller.process.stopped == 0


def test_tick_routes_poses_to_the_world_and_metrics_to_the_roster():
    controller = _controller("cam_0")
    controller.connect(controller.options)
    monitor = controller.monitors[-1]
    base = controller.app_config.base_topic
    monitor.queued = [
        (f"{base}/pose/7", {"tag_id": 7, "position_m": {"x": 1.0, "y": 2.0}}),
        (f"{base}/metrics", {"role": "coordinator", "poses_published": 5, "tracked_tags": [7]}),
    ]
    snapshot, lines, _ = controller.tick()
    assert [tag.tag_id for tag in controller.world.tags()] == [7]
    assert snapshot.poses_published == 5
    assert snapshot.tracked_tags == (7,)
    assert lines == []


def test_tick_logs_events_and_status_but_not_metrics():
    controller = _controller("cam_0")
    controller.connect(controller.options)
    monitor = controller.monitors[-1]
    base = controller.app_config.base_topic
    monitor.queued = [
        (f"{base}/event", {"code": "CALIBRATION_DRIFT", "message": "moved", "severity": "warning"}),
        (f"{base}/status", {"online": True, "reason": "running"}),
        (f"{base}/metrics", {"role": "coordinator"}),
    ]
    _, lines, _ = controller.tick()
    assert len(lines) == 2
    assert lines[0].startswith("[WARNING] CALIBRATION_DRIFT")
    assert lines[1].startswith("[status]")


def test_tick_expires_stale_tags_once_per_frame():
    clock = FakeClock()
    controller = _controller("cam_0", clock=clock)
    controller.connect(controller.options)
    monitor = controller.monitors[-1]
    base = controller.app_config.base_topic
    monitor.queued = [(f"{base}/pose/7", {"tag_id": 7, "position_m": {"x": 0.0, "y": 0.0}})]
    controller.tick()
    assert controller.world.tags() != []
    clock.advance(60 * PRESENCE_TIMEOUT_NS)
    controller.tick()
    assert controller.world.tags() == [], "the tick owns eviction now"


def test_tick_reports_a_calibration_reload():
    controller = _controller("cam_0")
    controller.connect(controller.options)
    controller.calibration_store.changed = True
    _, _, notices = controller.tick()
    assert _codes(notices) == [NoticeCode.CALIBRATIONS_RELOADED]


def test_snapshot_carries_the_frame_clock_and_process_state():
    clock = FakeClock()
    controller = _controller("cam_0", clock=clock)
    controller.process.running = True
    controller.process.pid = 99
    snapshot, _, _ = controller.tick()
    assert snapshot.now_ns == clock.now_ns
    assert (snapshot.process_running, snapshot.pid) == (True, 99)


def test_shutdown_is_idempotent_and_stops_both_transports():
    controller = _controller("cam_0")
    controller.connect(controller.options)
    monitor = controller.monitors[-1]
    controller.shutdown()
    controller.shutdown()
    assert controller.process.stopped == 2      # stop() is itself idempotent
    assert monitor.stopped == 1
    assert controller.monitor is None


@pytest.mark.parametrize("cameras", [("cam_0", "cam_1"), ("cam_0", "cam_1", "cam_2")])
def test_roster_follows_the_configured_cameras(cameras):
    controller = _controller(*cameras)
    controller.connect(controller.options)
    snapshot, _, _ = controller.tick()
    assert tuple(row.camera_id for row in snapshot.rows) == cameras


def test_roster_reads_the_camera_ids_from_the_config(tmp_path):
    from vision_system.apps.server_gui.controller import roster_from_config
    from vision_system.core.config import save_json

    config_path = tmp_path / "config.local.json"
    save_json(
        config_path,
        AppConfig(cameras=[CameraConfig(id="cam_0", source=0), CameraConfig(id="cam_1", source=1)]),
    )
    config, cameras = roster_from_config(config_path, tmp_path / "missing.json")
    assert cameras == ["cam_0", "cam_1"]
    assert config.base_topic == "vision/default/indoor-01"
