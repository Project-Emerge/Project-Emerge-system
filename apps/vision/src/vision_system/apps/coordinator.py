"""Central fusion server: receives node observations and publishes 3D fused poses."""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import threading
import time
from collections import Counter
from pathlib import Path

from ..core.config import AppConfig, initial_config
from ..pipeline.calibration_store import CalibrationStore
from ..pipeline.debug_renderer import DebugRenderer
from ..pipeline.fusion import FusedPose, FusionEngine
from ..pipeline.fusion_window import ObservationWindow
from ..transport.diagnostics import configure_diagnostics, event
from ..transport.mqtt import MqttBridge, OfflineBridge
from ..transport.observations import reconstruct_observations

LOGGER = logging.getLogger(__name__)
METRICS_PERIOD_S = 1.0
COORDINATOR_LOOP_WAIT_S = 0.005
CAMERA_ONLINE_TIMEOUT_NS = 2_000_000_000
NANOSECONDS_PER_MILLISECOND = 1_000_000


class VisionCoordinator:
    """Fusion server: collects observations from camera nodes and publishes fused poses."""

    def __init__(
        self,
        config: AppConfig,
        calibration_dir: Path = Path("calibrations"),
        cache_path: Path = Path(".state/last_good_config.json"),
        mqtt_enabled: bool = True,
        debug: bool = False,
    ) -> None:
        self.config = config
        self.calibration_dir = calibration_dir
        self.cache_path = cache_path
        self.calibration_store = CalibrationStore(calibration_dir)
        self.calibrations = self.calibration_store.calibrations
        self.fusion = FusionEngine(config.fusion)
        self.observation_window = ObservationWindow(self.fusion)
        self.debug_renderer = DebugRenderer(config, self.calibrations)
        if mqtt_enabled:
            self.bridge: MqttBridge | OfflineBridge = MqttBridge(
                config,
                cache_path,
                calibration_dir,
                self._queue_config,
                extra_subscriptions={f"{config.base_topic}/observations/+": 0},
                on_extra_message=self._on_observation_message,
            )
        else:
            self.bridge = OfflineBridge(config)
        self.pending_config: queue.SimpleQueue[AppConfig] = queue.SimpleQueue()
        self.message_queue: queue.SimpleQueue[dict] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.last_seen_utc_ns: dict[str, int] = {}
        self.observations_received: Counter[str] = Counter()
        self.pose_count = 0
        self.diagnostic_observations: Counter[str] = Counter()
        self.utc_to_monotonic_ns = time.monotonic_ns() - time.time_ns()

    def _queue_config(self, config: AppConfig) -> None:
        self.pending_config.put(config)

    def _on_observation_message(self, topic: str, body: dict) -> None:
        self.message_queue.put(body)

    def _apply_pending_config(self) -> None:
        latest: AppConfig | None = None
        while True:
            try:
                latest = self.pending_config.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        self.config = latest
        self.fusion.update_config(latest.fusion)
        self.debug_renderer.update(latest, self.calibrations)
        self.observation_window.clear()

    def _reload_calibrations_if_changed(self) -> None:
        if not self.calibration_store.reload_if_changed():
            return
        self.calibrations = self.calibration_store.calibrations
        self.debug_renderer.update(self.config, self.calibrations)
        self.bridge.publish_event(
            "CALIBRATION_RELOADED", "Calibration artifacts reloaded", severity="info"
        )

    def _reconstruct(self, body: dict):
        return reconstruct_observations(
            body, self.calibrations, self.utc_to_monotonic_ns
        )

    def _tick(self) -> list[FusedPose]:
        self._apply_pending_config()
        self._reload_calibrations_if_changed()
        updated_tags: set[int] = set()
        while True:
            try:
                body = self.message_queue.get_nowait()
            except queue.Empty:
                break
            observations, reject_reason = self._reconstruct(body)
            if not observations:
                if reject_reason is not None:
                    self.diagnostic_observations[reject_reason] += 1
                continue
            camera_id = observations[0].camera_id
            self.observations_received[camera_id] += 1
            self.last_seen_utc_ns[camera_id] = observations[0].utc_ns
            for observation in observations:
                if (
                    observation.reprojection_error_px
                    <= self.config.fusion.max_reprojection_error_px
                ):
                    self.observation_window.add(observation)
                    updated_tags.add(observation.tag_id)
                    self.diagnostic_observations["accepted_for_fusion"] += 1
                else:
                    self.diagnostic_observations["reprojection_error_too_high"] += 1
        fused_poses, failures = self.observation_window.fuse(updated_tags)
        self.diagnostic_observations["fusion_failed"] += failures
        self.diagnostic_observations["fused"] += len(fused_poses)
        for pose in fused_poses:
            if self.observation_window.should_publish(pose.tag_id, pose.monotonic_ns):
                self.bridge.publish_pose(pose)
                self.observation_window.mark_published(pose.tag_id, pose.monotonic_ns)
                self.pose_count += 1
        now_ns = time.monotonic_ns()
        now_utc_ns = time.time_ns()
        for pose in self.observation_window.predict(updated_tags, now_ns, now_utc_ns):
            if not self.observation_window.should_publish(pose.tag_id, now_ns):
                continue
            self.bridge.publish_pose(pose)
            self.observation_window.mark_published(pose.tag_id, now_ns)
            self.pose_count += 1
            self.diagnostic_observations["predicted"] += 1
        return fused_poses

    def config_camera_ids(self) -> set[str]:
        return {camera.id for camera in self.config.cameras}

    def run(self) -> None:
        event(
            LOGGER,
            "coordinator_started",
            revision=self.config.revision,
            mqtt_enabled=not isinstance(self.bridge, OfflineBridge),
            calibration_files=sorted(self.calibrations),
            calibrated_cameras={
                camera_id: (
                    self.calibrations[camera_id].world_from_camera is not None
                    if camera_id in self.calibrations
                    else None
                )
                for camera_id in self.config_camera_ids()
            },
            reference_ids=sorted(self.config.references_by_id()),
        )
        self.bridge.start()
        missing = [
            camera_id
            for camera_id in self.config_camera_ids()
            if camera_id not in self.calibrations
        ]
        if missing:
            self.bridge.publish_event(
                "MISSING_CALIBRATION",
                "Cameras without calibration cannot contribute observations",
                severity="warning",
                cameras=missing,
            )
        last_metrics = 0.0
        try:
            while not self.stop_event.is_set():
                fused_poses = self._tick()
                if self.config.debug.world_view:
                    self.debug_renderer.render({}, {}, fused_poses)
                now = time.monotonic()
                if now - last_metrics >= METRICS_PERIOD_S:
                    self._publish_metrics()
                    last_metrics = now
                self.stop_event.wait(COORDINATOR_LOOP_WAIT_S)
        finally:
            self.bridge.stop()

    def _publish_metrics(self) -> None:
        now_utc_ns = time.time_ns()
        camera_status: dict[str, dict] = {}
        for camera_id in self.config_camera_ids():
            last_seen = self.last_seen_utc_ns.get(camera_id)
            camera_status[camera_id] = {
                "online": last_seen is not None
                and now_utc_ns - last_seen < CAMERA_ONLINE_TIMEOUT_NS,
                "observations_received": self.observations_received[camera_id],
                "calibrated": camera_id in self.calibrations,
                "age_ms": (
                    round(
                        (now_utc_ns - last_seen) / NANOSECONDS_PER_MILLISECOND,
                        1,
                    )
                    if last_seen is not None
                    else None
                ),
            }
            self.bridge.publish_camera_status(camera_id, camera_status[camera_id])
        self.bridge.publish_metrics(
            {
                "timestamp_ns": now_utc_ns,
                "role": "coordinator",
                "poses_published": self.pose_count,
                "tracked_tags": sorted(self.fusion.trackers),
                "cameras": camera_status,
            }
        )
        event(
            LOGGER,
            "coordinator_diagnostic_summary",
            poses_published=self.pose_count,
            tracked_tags=sorted(self.fusion.trackers),
            cameras=camera_status,
            observation_outcomes=dict(self.diagnostic_observations),
        )
        self.diagnostic_observations.clear()

    def stop(self) -> None:
        self.stop_event.set()


def coordinator_main() -> None:
    parser = argparse.ArgumentParser(
        description="VisionSystem fusion server: riceve osservazioni dai nodi e pubblica pose"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--config", type=Path, help="fallback locale; MQTT resta il canale primario"
    )
    parser.add_argument("--cache", type=Path, default=Path(".state/last_good_config.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument("--debug", action="store_true", help="mostra la vista world locale")
    parser.add_argument("--no-mqtt", action="store_true", help="esegue senza aprire socket MQTT")
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-server", verbose=args.verbose)
    print(f"Log diagnostico: {diagnostic_path}")
    config = initial_config(args.config, args.cache)
    if args.debug:
        config = config.model_copy(
            update={"debug": config.debug.model_copy(update={"world_view": True})}
        )
    coordinator = VisionCoordinator(
        config,
        calibration_dir=args.calibrations,
        cache_path=args.cache,
        mqtt_enabled=not args.no_mqtt,
        debug=args.debug,
    )
    signal.signal(signal.SIGINT, lambda signum, frame: coordinator.stop())
    signal.signal(signal.SIGTERM, lambda signum, frame: coordinator.stop())
    coordinator.run()
