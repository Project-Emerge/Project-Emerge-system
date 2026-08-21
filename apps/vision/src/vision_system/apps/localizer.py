"""Standalone single-machine runtime: 4-camera capture, detection, and local fusion."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2

from ..core.config import AppConfig
from ..pipeline.calibration_store import CalibrationStore, calibration_compatibility_error
from ..pipeline.capture import CaptureManager, Frame
from ..pipeline.debug_renderer import DebugRenderer
from ..pipeline.detection import MarkerDetector, TagObservation
from ..pipeline.drift import ReferenceDriftMonitor
from ..pipeline.fusion import FusionEngine
from ..pipeline.fusion_window import ObservationWindow
from ..transport.diagnostics import event
from ..transport.mqtt import MqttBridge, OfflineBridge

LOGGER = logging.getLogger(__name__)
DETECTION_WORKERS = 4
METRICS_PERIOD_S = 1.0
ACTIVE_LOOP_WAIT_S = 0.001
IDLE_LOOP_WAIT_S = 0.01


class VisionRuntime:
    def __init__(
        self,
        config: AppConfig,
        calibration_dir: Path = Path("calibrations"),
        cache_path: Path = Path(".state/last_good_config.json"),
        mqtt_enabled: bool = True,
        print_poses: bool = False,
        allow_low_quality: bool = False,
    ) -> None:
        self.config = config
        self.calibration_dir = calibration_dir
        self.cache_path = cache_path
        self.allow_low_quality = allow_low_quality
        self.calibration_store = CalibrationStore(calibration_dir)
        self.calibrations = self.calibration_store.calibrations
        self.capture = CaptureManager(config.cameras)
        self.detectors = self._build_detectors()
        self.fusion = FusionEngine(config.fusion)
        self.observation_window = ObservationWindow(self.fusion)
        self.debug = DebugRenderer(config, self.calibrations)
        self.drift = ReferenceDriftMonitor(
            allow_low_quality=allow_low_quality, on_exclude=self._handle_camera_excluded
        )
        self.bridge: MqttBridge | OfflineBridge
        if mqtt_enabled:
            self.bridge = MqttBridge(config, cache_path, calibration_dir, self._queue_config)
        else:
            self.bridge = OfflineBridge(config, print_poses=print_poses)
        self.pending_config: queue.SimpleQueue[AppConfig] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.last_sequence: dict[str, int] = {}
        self.pose_count = 0
        self.diagnostic_frames: Counter[str] = Counter()
        self.diagnostic_detected_ids: dict[str, Counter[int]] = defaultdict(Counter)
        self.diagnostic_observations: Counter[str] = Counter()

    def _build_detectors(self) -> dict[str, MarkerDetector]:
        return {
            camera.id: MarkerDetector(self.config.aruco, self.calibrations)
            for camera in self.config.cameras
            if camera.id in self.calibrations
            and calibration_compatibility_error(camera, self.calibrations[camera.id]) is None
        }

    def _incompatible_calibrations(self) -> dict[str, str]:
        return {
            camera.id: reason
            for camera in self.config.cameras
            if (calibration := self.calibrations.get(camera.id)) is not None
            if (reason := calibration_compatibility_error(camera, calibration)) is not None
        }

    def _queue_config(self, config: AppConfig) -> None:
        self.pending_config.put(config)

    def _reload_calibrations_if_changed(self) -> None:
        if not self.calibration_store.reload_if_changed():
            return
        self.calibrations = self.calibration_store.calibrations
        self.detectors = self._build_detectors()
        self.debug.update(self.config, self.calibrations)
        self.drift.reset()
        self.bridge.publish_event(
            "CALIBRATION_RELOADED", "Calibration artifacts reloaded", severity="info"
        )

    def _apply_pending_config(self) -> None:
        latest: AppConfig | None = None
        while True:
            try:
                latest = self.pending_config.get_nowait()
            except queue.Empty:
                break
        if latest is None:
            return
        old_cameras = [camera.model_dump() for camera in self.config.cameras]
        new_cameras = [camera.model_dump() for camera in latest.cameras]
        self.config = latest
        self.fusion.update_config(latest.fusion)
        if old_cameras != new_cameras:
            self.capture.stop()
            self.capture = CaptureManager(latest.cameras)
            self.capture.start()
            self.last_sequence.clear()
        self.detectors = self._build_detectors()
        incompatible = self._incompatible_calibrations()
        if incompatible:
            self.bridge.publish_event(
                "INCOMPATIBLE_CALIBRATION",
                "Configuration changed camera geometry; recalibration required",
                severity="error",
                cameras=incompatible,
            )
        self.debug.update(latest, self.calibrations)
        self.observation_window.clear()

    def run(self) -> None:
        incompatible = self._incompatible_calibrations()
        missing = [
            camera.id for camera in self.config.cameras if camera.id not in self.calibrations
        ]
        event(
            LOGGER,
            "runtime_started",
            revision=self.config.revision,
            mqtt_enabled=not isinstance(self.bridge, OfflineBridge),
            allow_low_quality=self.allow_low_quality,
            camera_sources={camera.id: camera.source for camera in self.config.cameras},
            calibration_files=sorted(self.calibrations),
            calibration_source_matches={
                camera.id: (
                    self.calibrations[camera.id].source == camera.source
                    if camera.id in self.calibrations
                    else None
                )
                for camera in self.config.cameras
            },
            missing_calibrations=missing,
            incompatible_calibrations=incompatible,
            active_detectors=sorted(self.detectors),
            reference_ids=sorted(self.config.references_by_id()),
            mobile_ids=[marker.id for marker in self.config.aruco.mobile_markers],
            auto_mobile_enabled=self.config.aruco.auto_mobile_markers.enabled,
        )
        self.bridge.start()
        self.capture.start()
        if missing:
            self.bridge.publish_event(
                "MISSING_CALIBRATION",
                "Cameras without calibration are excluded",
                severity="error",
                cameras=missing,
            )
        if incompatible:
            self.bridge.publish_event(
                "INCOMPATIBLE_CALIBRATION",
                "Cameras excluded until calibration is repeated with current video settings",
                severity="error",
                cameras=incompatible,
            )
        executor = ThreadPoolExecutor(max_workers=DETECTION_WORKERS, thread_name_prefix="aruco")
        last_metrics = 0.0
        try:
            while not self.stop_event.is_set():
                self._apply_pending_config()
                self._reload_calibrations_if_changed()
                frames = self.capture.latest()
                new_frames = {
                    camera_id: frame
                    for camera_id, frame in frames.items()
                    if self.last_sequence.get(camera_id) != frame.sequence
                    and camera_id in self.detectors
                    and camera_id not in self.drift.excluded_cameras
                }
                for camera_id, frame in new_frames.items():
                    self.last_sequence[camera_id] = frame.sequence
                observations_by_camera: dict[str, list[TagObservation]] = defaultdict(list)
                futures = {
                    executor.submit(
                        self.detectors[camera_id].detect,
                        camera_id,
                        frame.image,
                        frame.monotonic_ns,
                        frame.utc_ns,
                    ): (camera_id, frame)
                    for camera_id, frame in new_frames.items()
                }
                updated_tags: set[int] = set()
                for future in as_completed(futures):
                    camera_id, frame = futures[future]
                    try:
                        observations, corners, ids = future.result()
                    except Exception:
                        LOGGER.exception("detection failed for %s", camera_id)
                        continue
                    observations_by_camera[camera_id] = observations
                    self.diagnostic_frames[camera_id] += 1
                    if ids is not None:
                        self.diagnostic_detected_ids[camera_id].update(
                            int(marker_id) for marker_id in ids.reshape(-1)
                        )
                    self._check_reference_drift(camera_id, frame, corners, ids)
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
                for pose in self.observation_window.predict(
                    updated_tags, now_ns, now_utc_ns
                ):
                    if not self.observation_window.should_publish(pose.tag_id, now_ns):
                        continue
                    self.bridge.publish_pose(pose)
                    self.observation_window.mark_published(pose.tag_id, now_ns)
                    self.pose_count += 1
                    self.diagnostic_observations["predicted"] += 1
                if self.config.debug.mosaic or self.config.debug.world_view:
                    self.debug.render(frames, observations_by_camera, fused_poses)
                now = time.monotonic()
                if now - last_metrics >= METRICS_PERIOD_S:
                    self._publish_metrics()
                    last_metrics = now
                self.stop_event.wait(ACTIVE_LOOP_WAIT_S if new_frames else IDLE_LOOP_WAIT_S)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            self.capture.stop()
            self.bridge.stop()
            cv2.destroyAllWindows()

    def stop(self) -> None:
        self.stop_event.set()

    def _check_reference_drift(self, camera_id: str, frame: Frame, corners, ids) -> None:
        self.drift.check(
            camera_id,
            corners,
            ids,
            self.calibrations.get(camera_id),
            self.config.references_by_id(),
        )

    def _handle_camera_excluded(self, camera_id: str, status: dict) -> None:
        self.bridge.publish_event(
            "CALIBRATION_DRIFT",
            "Camera excluded until recalibration",
            severity="error",
            camera_id=camera_id,
            translation_error_m=status["translation_error_m"],
            rotation_error_deg=status["rotation_error_deg"],
            references=status["used_reference_ids"],
        )

    def _publish_metrics(self) -> None:
        camera_status = self.capture.status()
        for camera_id, status in camera_status.items():
            status["excluded_for_drift"] = camera_id in self.drift.excluded_cameras
            status["calibrated"] = camera_id in self.calibrations
            self.bridge.publish_camera_status(camera_id, status)
        self.bridge.publish_metrics(
            {
                "timestamp_ns": time.time_ns(),
                "poses_published": self.pose_count,
                "tracked_tags": sorted(self.fusion.trackers),
                "excluded_cameras": sorted(self.drift.excluded_cameras),
                "cameras": camera_status,
            }
        )
        event(
            LOGGER,
            "runtime_diagnostic_summary",
            poses_published=self.pose_count,
            tracked_tags=sorted(self.fusion.trackers),
            excluded_cameras=sorted(self.drift.excluded_cameras),
            cameras=camera_status,
            frames_processed=dict(self.diagnostic_frames),
            detected_marker_frame_counts={
                camera_id: dict(counts)
                for camera_id, counts in self.diagnostic_detected_ids.items()
            },
            observation_outcomes=dict(self.diagnostic_observations),
            reference_checks=self.drift.reference_check_status,
        )
        self.diagnostic_frames.clear()
        self.diagnostic_detected_ids.clear()
        self.diagnostic_observations.clear()
