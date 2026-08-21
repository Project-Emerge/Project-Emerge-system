"""Calibration artifact management, geometry validation, and atomic hot-reloading."""

from __future__ import annotations

import math
import time
from pathlib import Path

from ..calibration.store import calibration_dir_signature, load_calibrations
from ..core.config import CameraCalibration, CameraConfig

CALIBRATION_RESCAN_PERIOD_S = 2.0
ZOOM_TOLERANCE = 1e-6


def calibration_compatibility_error(
    camera: CameraConfig, calibration: CameraCalibration
) -> str | None:
    """Explain why an artifact cannot be used with the current camera geometry."""
    if calibration.image_size != (camera.width, camera.height):
        return "image size differs"
    configured_zoom = camera.zoom
    calibrated_zoom = calibration.camera_zoom
    if configured_zoom is not None or calibrated_zoom is not None:
        if configured_zoom is None or calibrated_zoom is None:
            return "zoom differs"
        if not math.isclose(
            configured_zoom,
            calibrated_zoom,
            rel_tol=0,
            abs_tol=ZOOM_TOLERANCE,
        ):
            return "zoom differs"
    if not math.isclose(
        camera.digital_zoom,
        calibration.camera_digital_zoom,
        rel_tol=0,
        abs_tol=ZOOM_TOLERANCE,
    ):
        return "digital zoom differs"
    return None


class CalibrationStore:
    """Loads calibration artifacts and detects atomic changes on disk."""

    def __init__(
        self,
        directory: Path,
        rescan_period_s: float = CALIBRATION_RESCAN_PERIOD_S,
    ) -> None:
        self.directory = directory
        self.rescan_period_s = rescan_period_s
        self.calibrations = load_calibrations(directory)
        self.signature = calibration_dir_signature(directory)
        self.last_scan = 0.0

    def reload_if_changed(self) -> bool:
        now = time.monotonic()
        if now - self.last_scan < self.rescan_period_s:
            return False
        self.last_scan = now
        signature = calibration_dir_signature(self.directory)
        if signature == self.signature:
            return False
        self.calibrations = load_calibrations(self.directory)
        self.signature = signature
        return True
