"""Multi-camera pose fusion with Huber loss minimization and motion tracking."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from ..core.config import FusionConfig
from ..core.geometry import (
    average_transforms,
    matrix_to_quaternion,
    normalize_quaternion,
    pose_matrix,
    slerp,
)
from .detection import TagObservation, camera_from_world

NANOSECONDS_PER_SECOND = 1_000_000_000
MIN_ROTATION_ANGLE_RAD = 1e-9
MIN_UPDATE_INTERVAL_S = 1e-3
MIN_QUALITY_SCALE = 0.4
QUALITY_SCALE_RANGE = 0.6
ANGULAR_VELOCITY_HISTORY_WEIGHT = 0.8
ANGULAR_VELOCITY_MEASUREMENT_WEIGHT = 0.2
FULL_QUALITY_MARKER_SIDE_PX = 60.0
FULL_QUALITY_CAMERA_COUNT = 2
QUALITY_ERROR_DECAY_SCALE = 2.0
ROTATION_DISTANCE_WEIGHT = 0.1
OPTIMIZER_MAX_EVALUATIONS = 30
MIN_FILTER_CUTOFF_HZ = 1e-6


@dataclass
class FusedPose:
    tag_id: int
    monotonic_ns: int
    utc_ns: int
    world_from_tag: NDArray[np.float64]
    position_m: NDArray[np.float64]
    orientation_xyzw: NDArray[np.float64]
    velocity_m_s: NDArray[np.float64]
    angular_velocity_rad_s: NDArray[np.float64]
    cameras: list[str]
    reprojection_error_px: float
    quality: float
    predicted: bool = False
    # Cameras that saw the tag but were excluded for contradicting the consensus.
    rejected_cameras: list[str] = field(default_factory=list)
    # Widest gap between a single camera's own estimate and that consensus.
    camera_disagreement_m: float = 0.0


class OneEuroFilter:
    """Adaptive low-pass filter for vector-valued, irregularly sampled signals."""

    def __init__(self, value: NDArray[np.float64], timestamp_ns: int) -> None:
        self.reset(value, timestamp_ns)

    def reset(self, value: NDArray[np.float64], timestamp_ns: int) -> None:
        initial = np.asarray(value, dtype=np.float64)
        self.value = initial.copy()
        self.raw_value = initial.copy()
        self.derivative = np.zeros_like(initial)
        self.timestamp_ns = timestamp_ns

    @staticmethod
    def _smoothing_factor(
        dt: float, cutoff_hz: float | NDArray[np.float64]
    ) -> float | NDArray[np.float64]:
        cutoff = np.maximum(cutoff_hz, MIN_FILTER_CUTOFF_HZ)
        return 1.0 / (1.0 + 1.0 / (2.0 * math.pi * cutoff * dt))

    def update(
        self,
        value: NDArray[np.float64],
        timestamp_ns: int,
        *,
        min_cutoff_hz: float,
        beta: float,
        derivative_cutoff_hz: float,
    ) -> NDArray[np.float64]:
        sample = np.asarray(value, dtype=np.float64)
        dt = max(
            (timestamp_ns - self.timestamp_ns) / NANOSECONDS_PER_SECOND,
            MIN_UPDATE_INTERVAL_S,
        )
        raw_derivative = (sample - self.raw_value) / dt
        derivative_alpha = self._smoothing_factor(dt, derivative_cutoff_hz)
        self.derivative += derivative_alpha * (raw_derivative - self.derivative)

        cutoff_hz = min_cutoff_hz + beta * np.abs(self.derivative)
        alpha = self._smoothing_factor(dt, cutoff_hz)
        self.value += alpha * (sample - self.value)
        self.raw_value = sample.copy()
        self.timestamp_ns = timestamp_ns
        return self.value.copy()


class PoseTracker:
    def __init__(self, transform: NDArray[np.float64], timestamp_ns: int) -> None:
        self.position = transform[:3, 3].copy()
        self.velocity = np.zeros(3)
        self.position_filter = OneEuroFilter(self.position, timestamp_ns)
        self.filter_mode: str | None = None
        self.quaternion = matrix_to_quaternion(transform[:3, :3])
        self.angular_velocity = np.zeros(3)
        self.timestamp_ns = timestamp_ns

    def predict(self, timestamp_ns: int) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        dt = max(0.0, (timestamp_ns - self.timestamp_ns) / NANOSECONDS_PER_SECOND)
        position = self.position + self.velocity * dt
        angle = float(np.linalg.norm(self.angular_velocity) * dt)
        quaternion = self.quaternion
        if angle > MIN_ROTATION_ANGLE_RAD:
            axis = self.angular_velocity / np.linalg.norm(self.angular_velocity)
            delta = np.concatenate((axis * math.sin(angle / 2), [math.cos(angle / 2)]))
            quaternion = quaternion_multiply(self.quaternion, delta)
        return position, normalize_quaternion(quaternion)

    def update(
        self,
        transform: NDArray[np.float64],
        timestamp_ns: int,
        config: FusionConfig,
        quality: float = 1.0,
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        if self.filter_mode != config.tracker_filter:
            self.position_filter.reset(self.position, self.timestamp_ns)
            if config.tracker_filter == "one_euro":
                self.velocity = np.zeros(3)
            self.filter_mode = config.tracker_filter
        predicted_position, predicted_quaternion = self.predict(timestamp_ns)
        measured_position = transform[:3, 3]
        measured_quaternion = matrix_to_quaternion(transform[:3, :3])
        dt = max(
            (timestamp_ns - self.timestamp_ns) / NANOSECONDS_PER_SECOND,
            MIN_UPDATE_INTERVAL_S,
        )
        innovation = measured_position - predicted_position
        distance = float(np.linalg.norm(innovation))
        if distance > config.tracker_max_innovation_m > 0:
            innovation *= config.tracker_max_innovation_m / distance
        quality_scale = MIN_QUALITY_SCALE + QUALITY_SCALE_RANGE * float(
            np.clip(quality, 0, 1)
        )
        if config.tracker_filter == "one_euro":
            clamped_measurement = predicted_position + innovation
            self.position = self.position_filter.update(
                clamped_measurement,
                timestamp_ns,
                min_cutoff_hz=config.one_euro_min_cutoff_hz * quality_scale,
                beta=config.one_euro_beta * quality_scale,
                derivative_cutoff_hz=config.one_euro_derivative_cutoff_hz,
            )
            # The filtered derivative is bounded by the current measurement trend;
            # unlike the legacy alpha-beta update it cannot accumulate indefinitely.
            self.velocity = self.position_filter.derivative.copy()
        else:
            gain = config.tracker_position_gain * quality_scale
            self.position = predicted_position + gain * innovation
            self.velocity = self.velocity + config.tracker_velocity_gain * innovation / dt
        if np.dot(predicted_quaternion, measured_quaternion) < 0:
            measured_quaternion = -measured_quaternion
        updated_quaternion = slerp(
            predicted_quaternion,
            measured_quaternion,
            config.tracker_orientation_gain * quality_scale,
        )
        delta_rotation = quaternion_multiply(
            quaternion_conjugate(predicted_quaternion), measured_quaternion
        )
        axis_angle = quaternion_to_axis_angle(delta_rotation)
        self.angular_velocity = (
            ANGULAR_VELOCITY_HISTORY_WEIGHT * self.angular_velocity
            + ANGULAR_VELOCITY_MEASUREMENT_WEIGHT * axis_angle / dt
        )
        self.quaternion = updated_quaternion
        self.timestamp_ns = timestamp_ns
        return self.position.copy(), self.quaternion.copy()


def quaternion_multiply(
    left: NDArray[np.float64], right: NDArray[np.float64]
) -> NDArray[np.float64]:
    x1, y1, z1, w1 = left
    x2, y2, z2, w2 = right
    return normalize_quaternion(
        np.array(
            [
                w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
                w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
                w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
                w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            ]
        )
    )


def quaternion_conjugate(quaternion: NDArray[np.float64]) -> NDArray[np.float64]:
    result = np.asarray(quaternion).copy()
    result[:3] *= -1
    return result


def quaternion_to_axis_angle(quaternion: NDArray[np.float64]) -> NDArray[np.float64]:
    value = normalize_quaternion(quaternion)
    if value[3] < 0:
        value = -value
    angle = 2 * math.acos(float(np.clip(value[3], -1, 1)))
    sine = math.sqrt(max(1 - value[3] * value[3], 0))
    if sine < 1e-8:
        return np.zeros(3)
    return value[:3] / sine * angle


class FusionEngine:
    def __init__(self, config: FusionConfig) -> None:
        self.config = config
        self.trackers: dict[int, PoseTracker] = {}

    def update_config(self, config: FusionConfig) -> None:
        self.config = config

    def fuse(self, observations: list[TagObservation]) -> FusedPose | None:
        if not observations:
            return None
        tag_id = observations[0].tag_id
        observations = [item for item in observations if item.tag_id == tag_id]
        target_ns = max(item.monotonic_ns for item in observations)
        target_utc_ns = max(item.utc_ns for item in observations)
        tracker = self.trackers.get(tag_id)
        velocity = tracker.velocity if tracker else np.zeros(3)
        predicted_transform: NDArray[np.float64] | None = None
        if tracker:
            predicted_position, predicted_quaternion = tracker.predict(target_ns)
            predicted_transform = pose_matrix(predicted_position, predicted_quaternion)
        candidates = self._select_candidates(observations, predicted_transform)
        for candidate, observation in zip(candidates, observations, strict=True):
            dt = (target_ns - observation.monotonic_ns) / NANOSECONDS_PER_SECOND
            candidate[:3, 3] += velocity * dt

        # A camera that contradicts the others is not describing this marker.
        # Optimizing over the whole set would let it drag the joint optimum
        # around from frame to frame, which is what makes a multi-camera track
        # shake while every camera on its own looks perfectly steady.
        kept, disagreement = self._agreeing_cameras(observations, candidates)
        rejected = [
            observation.camera_id
            for index, observation in enumerate(observations)
            if index not in kept
        ]
        observations = [observations[index] for index in kept]
        candidates = [candidates[index] for index in kept]

        initial = average_transforms(candidates)
        optimized, error = self._optimize(initial, observations, target_ns, velocity)
        if error > self.config.max_fused_reprojection_error_px:
            # The retained cameras still cannot agree on a single rigid pose.
            # Publishing this would be a fabricated position; let the caller
            # dead-reckon from the last trustworthy measurement instead.
            return None
        quality = self._quality(observations, error, disagreement)
        if tracker is None:
            tracker = PoseTracker(optimized, target_ns)
            self.trackers[tag_id] = tracker
            position = optimized[:3, 3].copy()
            quaternion = matrix_to_quaternion(optimized[:3, :3])
        else:
            position, quaternion = tracker.update(optimized, target_ns, self.config, quality)
        filtered_transform = pose_matrix(position, quaternion)
        return FusedPose(
            tag_id=tag_id,
            monotonic_ns=target_ns,
            utc_ns=target_utc_ns,
            world_from_tag=filtered_transform,
            position_m=position,
            orientation_xyzw=quaternion,
            velocity_m_s=tracker.velocity.copy(),
            angular_velocity_rad_s=tracker.angular_velocity.copy(),
            cameras=sorted({item.camera_id for item in observations}),
            reprojection_error_px=error,
            quality=quality,
            rejected_cameras=sorted(set(rejected)),
            camera_disagreement_m=disagreement,
        )

    def _select_candidates(
        self,
        observations: list[TagObservation],
        predicted_transform: NDArray[np.float64] | None,
    ) -> list[NDArray[np.float64]]:
        """Resolve each camera's planar pose ambiguity against a shared reference.

        ``SOLVEPNP_IPPE_SQUARE`` returns two poses that reproject almost
        identically, so picking per camera by reprojection error alone lets
        cameras disagree about which of the mirrored solutions is real. With a
        tracker running the prediction arbitrates; on the first sighting the
        camera that sees the marker biggest does.
        """
        reference = predicted_transform
        if reference is None:
            anchor = max(observations, key=lambda item: item.marker_side_px)
            reference = anchor.world_from_tag
        return [
            min(
                observation.candidate_world_from_tags or (observation.world_from_tag,),
                key=lambda item: self._pose_distance(reference, item),
            ).copy()
            for observation in observations
        ]

    def _agreeing_cameras(
        self,
        observations: list[TagObservation],
        candidates: list[NDArray[np.float64]],
    ) -> tuple[list[int], float]:
        """Indices of the cameras consistent with the consensus, and the spread."""
        if len(candidates) < 2:
            return list(range(len(candidates))), 0.0
        positions = np.array([candidate[:3, 3] for candidate in candidates])
        consensus = np.median(positions, axis=0)
        distances = np.linalg.norm(positions - consensus, axis=1)
        disagreement = float(np.max(distances))
        kept = [
            index
            for index, distance in enumerate(distances)
            if distance <= self.config.max_camera_disagreement_m
        ]
        if not kept:
            # An even number of mutually contradicting cameras puts the median
            # between them, so every camera looks like an outlier. Keep the one
            # with the closest view rather than dropping the tag entirely.
            kept = [int(np.argmax([item.marker_side_px for item in observations]))]
        return kept, disagreement

    def _quality(
        self,
        observations: list[TagObservation],
        error: float,
        disagreement: float,
    ) -> float:
        marker_pixels = float(np.mean([item.marker_side_px for item in observations]))
        return float(
            np.clip(
                math.exp(-error / QUALITY_ERROR_DECAY_SCALE)
                * min(1.0, marker_pixels / FULL_QUALITY_MARKER_SIDE_PX)
                * min(
                    1.0,
                    len({item.camera_id for item in observations})
                    / FULL_QUALITY_CAMERA_COUNT,
                )
                * math.exp(-disagreement / self.config.max_camera_disagreement_m),
                0,
                1,
            )
        )

    def predict(self, tag_id: int, monotonic_ns: int, utc_ns: int) -> FusedPose | None:
        """Dead-reckon a pose while the tag is unseen but not yet stale."""
        tracker = self.trackers.get(tag_id)
        if tracker is None:
            return None
        if monotonic_ns - tracker.timestamp_ns > self.config.stale_after_ms * 1e6:
            return None
        position, quaternion = tracker.predict(monotonic_ns)
        return FusedPose(
            tag_id=tag_id,
            monotonic_ns=monotonic_ns,
            utc_ns=utc_ns,
            world_from_tag=pose_matrix(position, quaternion),
            position_m=position,
            orientation_xyzw=quaternion,
            velocity_m_s=tracker.velocity.copy(),
            angular_velocity_rad_s=tracker.angular_velocity.copy(),
            cameras=[],
            reprojection_error_px=0.0,
            quality=0.0,
            predicted=True,
        )

    @staticmethod
    def _pose_distance(left: NDArray[np.float64], right: NDArray[np.float64]) -> float:
        translation = float(np.linalg.norm(left[:3, 3] - right[:3, 3]))
        rotation_delta = left[:3, :3].T @ right[:3, :3]
        angle = float(np.arccos(np.clip((np.trace(rotation_delta) - 1) / 2, -1, 1)))
        return translation + ROTATION_DISTANCE_WEIGHT * angle

    def _optimize(
        self,
        initial: NDArray[np.float64],
        observations: list[TagObservation],
        target_ns: int,
        velocity: NDArray[np.float64],
    ) -> tuple[NDArray[np.float64], float]:
        initial_rvec, _ = cv2.Rodrigues(initial[:3, :3])
        initial_vector = np.concatenate((initial_rvec.reshape(3), initial[:3, 3]))

        def residual(vector: NDArray[np.float64]) -> NDArray[np.float64]:
            rotation, _ = cv2.Rodrigues(vector[:3])
            world_from_tag = np.eye(4)
            world_from_tag[:3, :3] = rotation
            world_from_tag[:3, 3] = vector[3:]
            values: list[NDArray[np.float64]] = []
            for observation in observations:
                time_delta = (
                    target_ns - observation.monotonic_ns
                ) / NANOSECONDS_PER_SECOND
                pose_at_capture = world_from_tag.copy()
                pose_at_capture[:3, 3] -= velocity * time_delta
                camera_pose = camera_from_world(observation) @ pose_at_capture
                rvec, _ = cv2.Rodrigues(camera_pose[:3, :3])
                projected, _ = cv2.projectPoints(
                    observation.object_points,
                    rvec,
                    camera_pose[:3, 3],
                    observation.camera_matrix,
                    observation.distortion,
                )
                values.append((projected.reshape(4, 2) - observation.corners).reshape(-1))
            return np.concatenate(values)

        if len(observations) == 1:
            return initial, observations[0].reprojection_error_px
        result = least_squares(
            residual,
            initial_vector,
            loss="huber",
            f_scale=self.config.huber_scale_px,
            max_nfev=OPTIMIZER_MAX_EVALUATIONS,
        )
        rotation, _ = cv2.Rodrigues(result.x[:3])
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = result.x[3:]
        residuals = residual(result.x).reshape(-1, 2)
        error = float(np.sqrt(np.mean(np.sum(residuals * residuals, axis=1))))
        return transform, error
