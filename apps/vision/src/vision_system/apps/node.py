"""Single-camera peripheral node: marker detection and MQTT observation publishing."""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import threading
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from ..core.config import AppConfig, CameraConfig, initial_config
from ..pipeline.calibration_store import CalibrationStore, calibration_compatibility_error
from ..pipeline.capture import CaptureManager, Frame
from ..pipeline.detection import MarkerDetector, TagObservation
from ..pipeline.drift import ReferenceDriftMonitor
from ..transport.diagnostics import configure_diagnostics, event
from ..transport.mqtt import MqttBridge, OfflineBridge
from ..transport.observations import observations_payload

LOGGER = logging.getLogger(__name__)
METRICS_PERIOD_S = 1.0
ACTIVE_LOOP_WAIT_S = 0.001
IDLE_LOOP_WAIT_S = 0.01


class VisionNode:
    """One camera on one PC: detects ArUco markers and publishes only observations."""

    def __init__(
        self,
        config: AppConfig,
        camera_id: str,
        calibration_dir: Path = Path("calibrations"),
        cache_path: Path = Path(".state/last_good_config.json"),
        mqtt_enabled: bool = True,
        print_observations: bool = False,
        allow_low_quality: bool = False,
        debug: bool = False,
    ) -> None:
        self.config = config
        self.camera_id = camera_id
        self.calibration_dir = calibration_dir
        self.cache_path = cache_path
        self.allow_low_quality = allow_low_quality
        self.debug_enabled = debug
        self.calibration_store = CalibrationStore(calibration_dir)
        self.calibrations = self.calibration_store.calibrations
        self.camera: CameraConfig | None = self._own_camera(config)
        self.capture: CaptureManager | None = CaptureManager([self.camera])
        self.detector = self._build_detector()
        self.drift = ReferenceDriftMonitor(
            allow_low_quality=allow_low_quality, on_exclude=self._handle_excluded
        )
        self.bridge: MqttBridge | OfflineBridge
        if mqtt_enabled:
            self.bridge = MqttBridge(config, cache_path, calibration_dir, self._queue_config)
        else:
            self.bridge = OfflineBridge(config, print_observations=print_observations)
        self.pending_config: queue.SimpleQueue[AppConfig] = queue.SimpleQueue()
        self.stop_event = threading.Event()
        self.last_sequence = -1
        self.last_publish_ns = 0
        self.observation_count = 0
        self.diagnostic_frames = 0
        self.diagnostic_detected_ids: Counter[int] = Counter()
        self.diagnostic_observations: Counter[str] = Counter()

    def _own_camera(self, config: AppConfig) -> CameraConfig:
        for camera in config.cameras:
            if camera.id == self.camera_id:
                return camera
        raise ValueError(f"camera {self.camera_id} non presente nella configurazione")

    def _build_detector(self) -> MarkerDetector | None:
        if self.camera is None:
            return None
        calibration = self.calibrations.get(self.camera_id)
        if calibration is None or calibration_compatibility_error(self.camera, calibration):
            return None
        return MarkerDetector(self.config.aruco, self.calibrations)

    def _queue_config(self, config: AppConfig) -> None:
        self.pending_config.put(config)

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
        cameras = [camera.model_dump() for camera in latest.cameras]
        if self.camera_id not in {camera.id for camera in latest.cameras}:
            if self.capture is not None:
                self.capture.stop()
            self.capture = None
            self.camera = None
            self.detector = None
            self.bridge.publish_event(
                "MISSING_CAMERA",
                f"Camera {self.camera_id} non presente nella nuova configurazione",
                severity="error",
                cameras=cameras,
            )
            return
        new_camera = next(camera for camera in latest.cameras if camera.id == self.camera_id)
        restart = self.camera is None or self.camera.model_dump() != new_camera.model_dump()
        self.camera = new_camera
        if restart:
            if self.capture is not None:
                self.capture.stop()
            self.capture = CaptureManager([new_camera])
            self.capture.start()
            self.last_sequence = -1
        self.detector = self._build_detector()

    def _reload_calibrations_if_changed(self) -> None:
        if not self.calibration_store.reload_if_changed():
            return
        self.calibrations = self.calibration_store.calibrations
        self.detector = self._build_detector()
        self.drift.reset()
        self.bridge.publish_event(
            "CALIBRATION_RELOADED", "Calibration artifacts reloaded", severity="info"
        )

    def run(self) -> None:
        calibration = self.calibrations.get(self.camera_id)
        incompatible = (
            calibration_compatibility_error(self.camera, calibration) if calibration else None
        )
        event(
            LOGGER,
            "node_started",
            camera_id=self.camera_id,
            revision=self.config.revision,
            mqtt_enabled=not isinstance(self.bridge, OfflineBridge),
            allow_low_quality=self.allow_low_quality,
            source=self.camera.source,
            calibration_source_matches=(
                calibration.source == self.camera.source if calibration else None
            ),
            missing_calibration=calibration is None,
            incompatible_calibration=incompatible,
        )
        self.bridge.start()
        if self.capture is not None:
            self.capture.start()
        if calibration is None:
            self.bridge.publish_event(
                "MISSING_CALIBRATION",
                "Camera without calibration is excluded",
                severity="error",
                cameras=[self.camera_id],
            )
        if incompatible:
            self.bridge.publish_event(
                "INCOMPATIBLE_CALIBRATION",
                "Camera excluded until calibration is repeated with current video settings",
                severity="error",
                cameras={self.camera_id: incompatible},
            )
        last_metrics = 0.0
        try:
            while not self.stop_event.is_set():
                self._apply_pending_config()
                self._reload_calibrations_if_changed()
                frame = self._latest_frame()
                if frame is not None:
                    self._process_frame(frame)
                now = time.monotonic()
                if now - last_metrics >= METRICS_PERIOD_S:
                    self._publish_metrics()
                    last_metrics = now
                self.stop_event.wait(
                    ACTIVE_LOOP_WAIT_S if frame is not None else IDLE_LOOP_WAIT_S
                )
        finally:
            if self.capture is not None:
                self.capture.stop()
            self.bridge.stop()
            cv2.destroyAllWindows()

    def _latest_frame(self) -> Frame | None:
        if self.capture is None:
            return None
        return self.capture.latest().get(self.camera_id)

    def _process_frame(self, frame: Frame) -> None:
        if frame.sequence == self.last_sequence:
            return
        self.last_sequence = frame.sequence
        if self.detector is None:
            return
        observations, corners, ids = self.detector.detect(
            self.camera_id, frame.image, frame.monotonic_ns, frame.utc_ns
        )
        self.diagnostic_frames += 1
        if ids is not None:
            self.diagnostic_detected_ids.update(int(value) for value in ids.reshape(-1))
        self.drift.check(
            self.camera_id,
            corners,
            ids,
            self.calibrations.get(self.camera_id),
            self.config.references_by_id(),
        )
        if self.debug_enabled:
            self._render_debug(frame, observations)
        accepted = [
            observation
            for observation in observations
            if observation.reprojection_error_px
            <= self.config.fusion.max_reprojection_error_px
        ]
        self.diagnostic_observations["accepted"] += len(accepted)
        self.diagnostic_observations["reprojection_error_too_high"] += (
            len(observations) - len(accepted)
        )
        if not accepted or self.camera_id in self.drift.excluded_cameras:
            return
        interval_ns = int(1e9 / self.config.fusion.publish_hz)
        if frame.monotonic_ns - self.last_publish_ns < interval_ns:
            return
        self.last_publish_ns = frame.monotonic_ns
        self.bridge.publish_observations(
            self.camera_id, observations_payload(self.camera_id, frame.utc_ns, accepted)
        )
        self.observation_count += len(accepted)

    def _render_debug(self, frame: Frame, observations: list[TagObservation]) -> None:
        image = frame.image.copy()
        for observation in observations:
            corners = observation.corners.astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(image, [corners], True, (0, 255, 0), 3)
            rvec, _ = cv2.Rodrigues(observation.camera_from_tag[:3, :3])
            cv2.drawFrameAxes(
                image,
                observation.camera_matrix,
                observation.distortion,
                rvec,
                observation.camera_from_tag[:3, 3],
                float(
                    np.linalg.norm(observation.object_points[1] - observation.object_points[0])
                ),
                2,
            )
            cv2.putText(
                image,
                f"ID {observation.tag_id} e={observation.reprojection_error_px:.2f}px",
                tuple(corners[0, 0]),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 255, 0),
                2,
            )
        try:
            cv2.imshow(f"VisionSystem - {self.camera_id}", image)
            cv2.waitKey(1)
        except cv2.error as error:
            LOGGER.error("debug window unavailable; disabling GUI: %s", error)
            self.debug_enabled = False

    def _handle_excluded(self, camera_id: str, status: dict) -> None:
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
        status: dict = {"online": False, "error": None, "frames_received": 0, "reconnects": 0}
        if self.capture is not None:
            status = next(iter(self.capture.status().values()), status)
        status["excluded_for_drift"] = self.camera_id in self.drift.excluded_cameras
        status["calibrated"] = self.camera_id in self.calibrations
        self.bridge.publish_camera_status(self.camera_id, status)
        self.bridge.publish_metrics(
            {
                "timestamp_ns": time.time_ns(),
                "role": "node",
                "camera_id": self.camera_id,
                "observations_published": self.observation_count,
                "excluded_for_drift": self.camera_id in self.drift.excluded_cameras,
            }
        )
        event(
            LOGGER,
            "node_diagnostic_summary",
            camera_id=self.camera_id,
            observations_published=self.observation_count,
            excluded_for_drift=self.camera_id in self.drift.excluded_cameras,
            frames_processed=self.diagnostic_frames,
            detected_marker_frame_counts=dict(self.diagnostic_detected_ids),
            observation_outcomes=dict(self.diagnostic_observations),
            reference_checks=self.drift.reference_check_status,
        )
        self.diagnostic_frames = 0
        self.diagnostic_detected_ids.clear()
        self.diagnostic_observations.clear()

    def stop(self) -> None:
        self.stop_event.set()


def node_main() -> None:
    parser = argparse.ArgumentParser(
        description="VisionSystem camera node: una camera, osservazioni ArUco via MQTT"
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--camera", required=True, help="camera gestita da questo PC (cam_0..cam_3)"
    )
    parser.add_argument(
        "--config", type=Path, help="fallback locale; MQTT resta il canale primario"
    )
    parser.add_argument("--cache", type=Path, default=Path(".state/last_good_config.json"))
    parser.add_argument("--calibrations", type=Path, default=Path("calibrations"))
    parser.add_argument(
        "--debug", action="store_true", help="mostra l'anteprima annotata della camera"
    )
    parser.add_argument("--no-mqtt", action="store_true", help="esegue senza aprire socket MQTT")
    parser.add_argument(
        "--print-observations",
        action="store_true",
        help="stampa un JSON per riga; richiede --no-mqtt",
    )
    parser.add_argument(
        "--allow-low-quality",
        action="store_true",
        help="tollera calibrazioni imperfette nel controllo drift",
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics(f"vision-node:{args.camera}", verbose=args.verbose)
    print(f"Log diagnostico: {diagnostic_path}")
    if args.print_observations and not args.no_mqtt:
        parser.error("--print-observations richiede --no-mqtt")
    config = initial_config(args.config, args.cache)
    try:
        node = VisionNode(
            config,
            args.camera,
            calibration_dir=args.calibrations,
            cache_path=args.cache,
            mqtt_enabled=not args.no_mqtt,
            print_observations=args.print_observations,
            allow_low_quality=args.allow_low_quality,
            debug=args.debug,
        )
    except ValueError as error:
        parser.error(str(error))
    signal.signal(signal.SIGINT, lambda signum, frame: node.stop())
    signal.signal(signal.SIGTERM, lambda signum, frame: node.stop())
    node.run()
