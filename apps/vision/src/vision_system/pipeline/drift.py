"""Continuous extrinsics drift monitoring against static reference markers."""

from __future__ import annotations

import time
from collections.abc import Callable

import numpy as np

from ..calibration.extrinsics import estimate_world_from_camera
from ..core.config import CameraCalibration, ReferenceMarkerConfig

CHECK_INTERVAL_S = 1.0
EXCLUSION_AFTER_S = 2.0
MAX_TRANSLATION_M = 0.02
MAX_ROTATION_DEG = 2.0


def evaluate_reference_drift(
    corners,
    ids,
    references: dict[int, ReferenceMarkerConfig],
    calibration: CameraCalibration | None,
    *,
    allow_low_quality: bool = False,
    max_translation_m: float = MAX_TRANSLATION_M,
    max_rotation_deg: float = MAX_ROTATION_DEG,
) -> dict:
    """Compare the reference markers seen in one frame against the saved extrinsics."""
    if calibration is None or calibration.world_from_camera is None or len(references) < 2:
        return {
            "status": "unavailable",
            "has_calibration": calibration is not None,
            "has_extrinsics": calibration is not None
            and calibration.world_from_camera is not None,
            "configured_reference_count": len(references),
        }
    seen = sorted(int(value) for value in ids.reshape(-1)) if ids is not None else []
    matched = sorted(set(seen) & set(references))
    estimate = estimate_world_from_camera(
        corners,
        ids,
        references,
        np.asarray(calibration.camera_matrix),
        np.asarray(calibration.distortion),
        allow_low_quality=allow_low_quality,
    )
    if estimate is None:
        return {
            "status": "pose_unavailable",
            "seen_ids": seen,
            "matched_reference_ids": matched,
            "likely_reason": (
                "fewer_than_two_references" if len(matched) < 2 else "solvepnp_ransac_failed"
            ),
        }
    measured, reprojection_error, used = estimate
    expected = np.asarray(calibration.world_from_camera)
    translation_error = float(np.linalg.norm(measured[:3, 3] - expected[:3, 3]))
    rotation_delta = expected[:3, :3].T @ measured[:3, :3]
    angle_error = float(
        np.degrees(np.arccos(np.clip((np.trace(rotation_delta) - 1) / 2, -1, 1)))
    )
    return {
        "status": "drift" if translation_error > max_translation_m or angle_error > max_rotation_deg
        else "ok",
        "seen_ids": seen,
        "matched_reference_ids": matched,
        "used_reference_ids": used,
        "reprojection_error_px": reprojection_error,
        "translation_error_m": translation_error,
        "rotation_error_deg": angle_error,
    }


class ReferenceDriftMonitor:
    """Rate-limited drift evaluation with exclusion after a sustained drift period."""

    def __init__(
        self,
        *,
        allow_low_quality: bool = False,
        on_exclude: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.allow_low_quality = allow_low_quality
        self.on_exclude = on_exclude
        self.excluded_cameras: set[str] = set()
        self.drift_since: dict[str, float] = {}
        self.last_reference_check: dict[str, float] = {}
        self.reference_check_status: dict[str, dict] = {}

    def check(
        self,
        camera_id: str,
        corners,
        ids,
        calibration: CameraCalibration | None,
        references: dict[int, ReferenceMarkerConfig],
    ) -> dict | None:
        """Evaluate drift for one frame; ``None`` when the check is rate-limited away."""
        now = time.monotonic()
        if now - self.last_reference_check.get(camera_id, 0.0) < CHECK_INTERVAL_S:
            return None
        self.last_reference_check[camera_id] = now
        status = evaluate_reference_drift(
            corners,
            ids,
            references,
            calibration,
            allow_low_quality=self.allow_low_quality,
        )
        self.reference_check_status[camera_id] = status
        if status["status"] == "drift":
            since = self.drift_since.setdefault(camera_id, now)
            if (
                not self.allow_low_quality
                and now - since >= EXCLUSION_AFTER_S
                and camera_id not in self.excluded_cameras
            ):
                self.excluded_cameras.add(camera_id)
                if self.on_exclude:
                    self.on_exclude(camera_id, status)
        else:
            self.drift_since.pop(camera_id, None)
        return status

    def reset(self) -> None:
        self.excluded_cameras.clear()
        self.drift_since.clear()
