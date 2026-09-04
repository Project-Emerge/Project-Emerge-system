"""Extrinsic camera pose estimation (world_from_camera) via solvePnP RANSAC."""

from __future__ import annotations

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import ReferenceMarkerConfig
from ..core.geometry import (
    invert_transform,
    marker_object_points,
    pose_matrix,
    transform_points,
)

# Legacy RANSAC thresholds – still used by diagnostic helpers and the wizard UI.
STRICT_EXTRINSIC_RANSAC_ERROR_PX = 3.0
LOW_QUALITY_EXTRINSIC_RANSAC_ERROR_PX = 30.0
# Geometric minimum: extrinsics need at least three distinct reference markers
# to constrain the planar solve properly.
MIN_REFERENCES_FOR_EXTRINSICS = 3
# Per-marker outlier gate: if a single marker's RMS reprojection error exceeds
# this threshold the entire marker (all 4 corners) is removed and the solve is
# re-run.  This avoids the RANSAC corner-level subset problem on coplanar anchors.
MARKER_OUTLIER_THRESHOLD_PX = 5.0
# Per-sample acceptance gate on the reprojection error.
MAX_SAMPLE_REPROJECTION_ERROR_PX = 2.0


def ransac_threshold_px(allow_low_quality: bool) -> float:
    return (
        LOW_QUALITY_EXTRINSIC_RANSAC_ERROR_PX
        if allow_low_quality
        else STRICT_EXTRINSIC_RANSAC_ERROR_PX
    )


def reference_correspondences(
    detected_corners,
    detected_ids: NDArray[np.int32] | None,
    references: dict[int, ReferenceMarkerConfig],
) -> tuple[NDArray[np.float32], NDArray[np.float32], list[int]]:
    """3-D world / 2-D pixel correspondences for the configured reference markers."""
    object_points: list[NDArray[np.float64]] = []
    image_points: list[NDArray[np.float32]] = []
    used: list[int] = []
    if detected_ids is None:
        return np.empty((0, 3), np.float32), np.empty((0, 2), np.float32), used
    for corners, marker_id_raw in zip(detected_corners, detected_ids.reshape(-1), strict=True):
        marker_id = int(marker_id_raw)
        reference = references.get(marker_id)
        if reference is None:
            continue
        world_from_tag = pose_matrix(
            np.asarray(reference.position_m), np.asarray(reference.orientation_xyzw)
        )
        object_points.append(
            transform_points(world_from_tag, marker_object_points(reference.size_m))
        )
        image_points.append(np.asarray(corners, dtype=np.float32).reshape(4, 2))
        used.append(marker_id)
    if not object_points:
        return np.empty((0, 3), np.float32), np.empty((0, 2), np.float32), used
    return (
        np.concatenate(object_points).astype(np.float32),
        np.concatenate(image_points).astype(np.float32),
        used,
    )


def estimate_world_from_camera(
    detected_corners,
    detected_ids: NDArray[np.int32] | None,
    references: dict[int, ReferenceMarkerConfig],
    camera_matrix: NDArray[np.float64],
    distortion: NDArray[np.float64],
    allow_low_quality: bool = False,
) -> tuple[NDArray[np.float64], float, list[int]] | None:
    """Camera pose in world coordinates from the visible reference markers."""
    object_points, image_points, used = reference_correspondences(
        detected_corners, detected_ids, references
    )
    if len(set(used)) < MIN_REFERENCES_FOR_EXTRINSICS:
        return None

    # Initial solve with SQPNP – robust for coplanar anchors, uses all points.
    valid, rvec, tvec = cv2.solvePnP(
        object_points,
        image_points,
        camera_matrix,
        distortion,
        flags=cv2.SOLVEPNP_SQPNP,
    )
    if not valid:
        return None

    # Per-marker outlier rejection (groups of 4 corners per marker).
    projected, _ = cv2.projectPoints(
        object_points, rvec, tvec, camera_matrix, distortion
    )
    residuals = projected.reshape(-1, 2) - image_points
    n_markers = len(used)
    marker_rms = np.array([
        float(np.sqrt(np.mean(np.sum(
            residuals[i * 4 : (i + 1) * 4] ** 2, axis=1
        ))))
        for i in range(n_markers)
    ])
    keep = [i for i, e in enumerate(marker_rms) if e < MARKER_OUTLIER_THRESHOLD_PX]
    if len(keep) < MIN_REFERENCES_FOR_EXTRINSICS:
        return None

    kept_idx = np.concatenate([np.arange(i * 4, (i + 1) * 4) for i in keep])
    obj_kept = object_points[kept_idx]
    img_kept = image_points[kept_idx]
    used_kept = [used[i] for i in keep]

    # Re-solve without outliers if any marker was removed.
    if len(keep) < n_markers:
        valid, rvec, tvec = cv2.solvePnP(
            obj_kept, img_kept, camera_matrix, distortion,
            flags=cv2.SOLVEPNP_SQPNP,
        )
        if not valid:
            return None

    # LM refinement on all kept points.
    rvec, tvec = cv2.solvePnPRefineLM(
        obj_kept, img_kept, camera_matrix, distortion, rvec, tvec,
    )

    rotation, _ = cv2.Rodrigues(rvec)
    camera_from_world = np.eye(4)
    camera_from_world[:3, :3] = rotation
    camera_from_world[:3, 3] = tvec.reshape(3)
    projected, _ = cv2.projectPoints(obj_kept, rvec, tvec, camera_matrix, distortion)
    residual = projected.reshape(-1, 2) - img_kept
    error = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
    return invert_transform(camera_from_world), error, used_kept


def extrinsic_sample_outcome(
    seen_ids: list[int],
    reference_ids: set[int],
    reprojection_error_px: float | None,
    allow_low_quality: bool = False,
) -> str:
    """Machine-readable frame classification used by telemetry counters."""
    if not seen_ids:
        return "no_marker_detected"
    matched = set(seen_ids) & reference_ids
    if len(matched) < MIN_REFERENCES_FOR_EXTRINSICS:
        return "fewer_than_two_references"
    if reprojection_error_px is None or not math.isfinite(reprojection_error_px):
        return "solvepnp_ransac_failed"
    if reprojection_error_px > MAX_SAMPLE_REPROJECTION_ERROR_PX:
        return "accepted_with_override" if allow_low_quality else "reprojection_error_over_2px"
    return "accepted"


def extrinsic_sample_diagnostic(
    seen_ids: list[int],
    reference_ids: set[int],
    reprojection_error_px: float | None,
    allow_low_quality: bool = False,
    ransac_error_px: float = STRICT_EXTRINSIC_RANSAC_ERROR_PX,
) -> tuple[bool, str, str]:
    """Human-readable accept/reject diagnostic for one extrinsic frame."""
    seen = sorted(set(seen_ids))
    matched = sorted(set(seen) & reference_ids)
    ignored = sorted(set(seen) - reference_ids)
    detection_line = (
        f"Visti {seen or 'nessuno'} | reference utili {matched or 'nessuno'} "
        f"| ignorati {ignored or 'nessuno'}"
    )
    outcome = extrinsic_sample_outcome(
        seen_ids, reference_ids, reprojection_error_px, allow_low_quality
    )
    if outcome == "no_marker_detected":
        return False, detection_line, "SCARTATO: nessun marker ArUco rilevato"
    if outcome == "fewer_than_two_references":
        return (
            False,
            detection_line,
            f"SCARTATO: reference utili {len(matched)}/{MIN_REFERENCES_FOR_EXTRINSICS}; "
            "target e ID ignorati non contano",
        )
    if outcome == "solvepnp_ransac_failed":
        return (
            False,
            detection_line,
            f"SCARTATO: solvePnP/RANSAC non trova una posa valida (soglia {ransac_error_px:.0f}px)",
        )
    if outcome == "reprojection_error_over_2px":
        return (
            False,
            detection_line,
            f"SCARTATO: errore {reprojection_error_px:.2f}px "
            f"> {MAX_SAMPLE_REPROJECTION_ERROR_PX:.2f}px",
        )
    if outcome == "accepted_with_override":
        return (
            True,
            detection_line,
            f"ACCETTATO con override: errore {reprojection_error_px:.2f}px",
        )
    return True, detection_line, f"ACCETTATO: errore {reprojection_error_px:.2f}px"
