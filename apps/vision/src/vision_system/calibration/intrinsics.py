"""Intrinsic matrix and rational distortion fitting with quality gating."""

from __future__ import annotations

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from .samples import CalibrationSample

# Intrinsic fit policy.
MIN_INTRINSIC_SAMPLES = 10
# Every Nth sample is held out for validation (1/5 = 20%).
VALIDATION_SPLIT_EVERY = 5
# Outlier views are dropped when their reprojection error exceeds
# median + OUTLIER_MAD_MULTIPLIER * MAD, but never below this floor...
OUTLIER_MIN_ERROR_PX = 1.0
OUTLIER_MAD_MULTIPLIER = 3.0
# ...and at least this fraction of the training views is always kept.
MIN_TRAINING_KEPT_FRACTION = 0.8
ERROR_PERCENTILE = 95.0

# Acceptance quality gates for a standard intrinsic calibration.
INTRINSIC_MAX_MEDIAN_ERROR_PX = 1.0
INTRINSIC_MAX_P95_ERROR_PX = 1.5


def intrinsic_quality_passed(median_error_px: float, p95_error_px: float) -> bool:
    return (
        median_error_px <= INTRINSIC_MAX_MEDIAN_ERROR_PX
        and p95_error_px <= INTRINSIC_MAX_P95_ERROR_PX
    )


def _view_error(
    object_points: NDArray[np.float32],
    image_points: NDArray[np.float32],
    camera_matrix: NDArray[np.float64],
    distortion: NDArray[np.float64],
) -> float:
    valid, rvec, tvec = cv2.solvePnP(
        object_points, image_points, camera_matrix, distortion, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not valid:
        return math.inf
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, camera_matrix, distortion)
    residual = projected.reshape(-1, 2) - image_points.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))


def calibrate_intrinsics(
    samples: list[CalibrationSample], image_size: tuple[int, int], board
) -> tuple[NDArray[np.float64], NDArray[np.float64], float, float]:
    """Fit camera matrix and distortion; returns (matrix, distortion, median, p95) errors."""
    if len(samples) < MIN_INTRINSIC_SAMPLES:
        raise ValueError(
            f"at least {MIN_INTRINSIC_SAMPLES} calibration samples are required"
        )
    chessboard = np.asarray(board.getChessboardCorners(), dtype=np.float32)

    def points(sample: CalibrationSample) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
        ids = sample.ids.reshape(-1)
        return chessboard[ids], sample.corners.reshape(-1, 2).astype(np.float32)

    validation_indices = {
        index for index in range(len(samples)) if index % VALIDATION_SPLIT_EVERY == 0
    }
    training = [sample for index, sample in enumerate(samples) if index not in validation_indices]
    validation = [sample for index, sample in enumerate(samples) if index in validation_indices]

    def fit(selected: list[CalibrationSample]):
        object_points, image_points = zip(*(points(sample) for sample in selected), strict=True)
        _, camera_matrix, distortion, _, _ = cv2.calibrateCamera(
            list(object_points),
            list(image_points),
            image_size,
            None,
            None,
            flags=cv2.CALIB_RATIONAL_MODEL,
        )
        return camera_matrix, distortion

    camera_matrix, distortion = fit(training)
    training_errors = np.array(
        [_view_error(*points(sample), camera_matrix, distortion) for sample in training]
    )
    median = float(np.median(training_errors))
    mad = float(np.median(np.abs(training_errors - median)))
    threshold = max(OUTLIER_MIN_ERROR_PX, median + OUTLIER_MAD_MULTIPLIER * mad)
    filtered = [
        sample
        for sample, error in zip(training, training_errors, strict=True)
        if error <= threshold
    ]
    minimum_kept = math.ceil(len(training) * MIN_TRAINING_KEPT_FRACTION)
    if len(filtered) < minimum_kept:
        order = np.argsort(training_errors)[:minimum_kept]
        filtered = [training[int(index)] for index in order]
    camera_matrix, distortion = fit(filtered)
    held_out = validation or filtered
    errors = np.array(
        [_view_error(*points(sample), camera_matrix, distortion) for sample in held_out]
    )
    return (
        camera_matrix,
        distortion.reshape(-1),
        float(np.median(errors)),
        float(np.percentile(errors, ERROR_PERCENTILE)),
    )
