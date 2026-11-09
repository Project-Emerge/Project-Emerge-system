"""Panel behaviour without a widget in sight.

The controller owns the process, the MQTT listener and the two models, and turns
one tick into a snapshot plus a list of console lines. It formats nothing and
touches no Tk object, so the whole behaviour of the panel is reachable from a test
with no display: build one with fake collaborators, call ``tick`` and assert on
what comes back.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from ...core.config import AppConfig
from ...monitoring.deployment import DeploymentStatus
from ...monitoring.listener import StatusMonitor
from ...monitoring.world import WorldModel
from ...pipeline.calibration_store import CalibrationStore
from ...transport.mqtt import MqttSettings
from ...transport.payloads import PoseUpdate, parse
from .models import LauncherFields, Notice, NoticeCode, PanelSnapshot
from .presenters import describe_message
from .supervisor import DEFAULT_CACHE, DEFAULT_CALIBRATIONS, ServerLaunchOptions, ServerProcess


def options_from_fields(
    fields: LauncherFields, fallback: ServerLaunchOptions
) -> tuple[ServerLaunchOptions, list[Notice]]:
    """Validate the launcher form. Pure: the port rule used to live in a widget reader."""
    notices: list[Notice] = []
    try:
        port = int(fields.port)
    except ValueError:
        port = fallback.mqtt_port
        notices.append(Notice(NoticeCode.INVALID_PORT))
    config = fields.config.strip()
    options = ServerLaunchOptions(
        config=Path(config) if config else None,
        calibrations=Path(fields.calibrations.strip() or DEFAULT_CALIBRATIONS),
        cache=Path(fields.cache.strip() or DEFAULT_CACHE),
        mqtt_host=fields.host.strip() or "localhost",
        mqtt_port=port,
        debug=fields.debug,
        no_mqtt=fields.no_mqtt,
        verbose=fields.verbose,
    )
    return options, notices


def roster_from_config(config_path: Path | None, cache_path: Path) -> tuple[AppConfig, list[str]]:
    from ...core.config import initial_config

    config = initial_config(config_path, cache_path)
    return config, [camera.id for camera in config.cameras]


class ServerPanelController:
    """Owns the panel's state and advances it one tick at a time."""

    def __init__(
        self,
        options: ServerLaunchOptions,
        cameras: Sequence[str] = (),
        *,
        process: ServerProcess | None = None,
        monitor_factory: Callable[[str, MqttSettings], StatusMonitor] = StatusMonitor,
        store_factory: Callable[[Path], CalibrationStore] = CalibrationStore,
        roster_loader: Callable[
            [Path | None, Path], tuple[AppConfig, list[str]]
        ] = roster_from_config,
        settings_factory: Callable[[], MqttSettings] = MqttSettings.from_environment,
        clock: Callable[[], int] = time.monotonic_ns,
        executor: Executor | None = None,
    ) -> None:
        self.options = options
        self.process = process if process is not None else ServerProcess()
        self.monitor: StatusMonitor | None = None
        self.status = DeploymentStatus(cameras)
        self.world = WorldModel()
        self.app_config: AppConfig | None = None
        self.calibration_store: CalibrationStore | None = None
        self._monitor_factory = monitor_factory
        self._store_factory = store_factory
        self._roster_loader = roster_loader
        self._settings_factory = settings_factory
        self._clock = clock
        self._owns_executor = executor is None
        self._executor = executor if executor is not None else ThreadPoolExecutor(max_workers=1)

    # ----------------------------------------------------------------- actions
    def connect(self, options: ServerLaunchOptions) -> list[Notice]:
        """(Re)subscribe to the deployment topics implied by the current config."""
        self.options = options
        notices: list[Notice] = []
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        try:
            config, cameras = self._roster_loader(options.config, options.cache)
        except (OSError, ValueError) as error:
            return [Notice(NoticeCode.CONFIG_UNREADABLE, {"error": error})]
        # AppConfig() falls back to a four-camera roster. On a two-camera rig that
        # would silently draw two cameras that do not exist, so say so instead.
        if options.config is None and not options.cache.exists():
            notices.append(Notice(NoticeCode.DEFAULT_ROSTER, {"cameras": ", ".join(cameras)}))
        self.status = DeploymentStatus(cameras)
        self.app_config = config
        self.world.reset()
        self.calibration_store = self._store_factory(options.calibrations)
        self.world.update_scene(config, self.calibration_store.calibrations)
        environment = self._settings_factory()
        settings = MqttSettings(
            host=options.mqtt_host,
            port=options.mqtt_port,
            username=environment.username,
            password=environment.password,
            tls=environment.tls,
        )
        self.monitor = self._monitor_factory(config.base_topic, settings)
        self.monitor.start()
        notices.append(
            Notice(
                NoticeCode.LISTENING,
                {"topic": config.base_topic, "cameras": list(cameras)},
            )
        )
        return notices

    def start_server(self, options: ServerLaunchOptions) -> list[Notice]:
        if self.process.running:
            return []
        self.options = options
        notices: list[Notice] = []
        if self.status.coordinator_online(self._clock()):
            notices.append(Notice(NoticeCode.SECOND_COORDINATOR))
        try:
            self.process.start(options)
        except (OSError, RuntimeError) as error:
            notices.append(Notice(NoticeCode.START_FAILED, {"error": error}))
            return notices
        # Reconnecting resets the roster view, so only do it when the launcher fields
        # no longer match the broker the panel is listening to.
        if self.monitor is None or (self.monitor.settings.host, self.monitor.settings.port) != (
            options.mqtt_host,
            options.mqtt_port,
        ):
            notices.extend(self.connect(options))
        return notices

    def stop_server(self) -> list[Notice]:
        """Ask the server to stop, off the caller's thread.

        ServerProcess.stop() waits up to 10 s for SIGTERM and 10 s more for SIGKILL.
        Run on the UI thread that froze the whole window; the tick already derives
        the buttons from process.running, so the panel converges on its own.
        """
        if not self.process.running:
            return []
        self._executor.submit(self.process.stop)
        return [Notice(NoticeCode.STOPPING)]

    # -------------------------------------------------------------------- tick
    def tick(self, now_ns: int | None = None) -> tuple[PanelSnapshot, list[str], list[Notice]]:
        """Drain both transports, advance the models, and describe the result."""
        now_ns = self._clock() if now_ns is None else now_ns
        lines = self.process.drain_logs()
        notices: list[Notice] = []
        if self.monitor is not None:
            for topic, body in self.monitor.drain():
                message = parse(topic, body)
                if message is None:
                    continue
                if isinstance(message, PoseUpdate):
                    self.world.apply(message, now_ns)
                    continue
                self.status.apply(message, now_ns)
                line = describe_message(message)
                if line:
                    lines.append(line)
        self.world.expire(now_ns)
        # Extrinsics change while calibrating a node: the store throttles the rescan
        # itself, so polling every tick costs nothing.
        if self.calibration_store is not None and self.calibration_store.reload_if_changed():
            self.world.update_scene(self.app_config, self.calibration_store.calibrations)
            notices.append(Notice(NoticeCode.CALIBRATIONS_RELOADED))
        return self.snapshot(now_ns), lines, notices

    def snapshot(self, now_ns: int) -> PanelSnapshot:
        monitor = self.monitor
        return PanelSnapshot(
            now_ns=now_ns,
            process_running=self.process.running,
            pid=self.process.pid,
            exit_code=self.process.exit_code,
            broker=None if monitor is None else (monitor.settings.host, monitor.settings.port),
            broker_connected=monitor is not None and monitor.connected.is_set(),
            broker_error=None if monitor is None else monitor.error,
            coordinator_online=self.status.coordinator_online(now_ns),
            poses_published=self.status.poses_published,
            tracked_tags=tuple(self.status.tracked_tags),
            rows=tuple(self.status.rows(now_ns)),
        )

    def shutdown(self) -> None:
        """Stop everything synchronously: this must finish before the process exits."""
        self.process.stop()
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        if self._owns_executor:
            self._executor.shutdown(wait=False)
