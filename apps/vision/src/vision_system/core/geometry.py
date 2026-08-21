"""3D rigid transforms, quaternion math, rotations, and SLERP interpolation."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


def normalize_quaternion(quaternion: NDArray[np.floating]) -> FloatArray:
    value = np.asarray(quaternion, dtype=np.float64)
    norm = float(np.linalg.norm(value))
    if norm < 1e-12:
        raise ValueError("zero quaternion")
    return value / norm


def quaternion_to_matrix(quaternion_xyzw: NDArray[np.floating]) -> FloatArray:
    x, y, z, w = normalize_quaternion(quaternion_xyzw)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def matrix_to_quaternion(matrix: NDArray[np.floating]) -> FloatArray:
    rotation = np.asarray(matrix, dtype=np.float64)[:3, :3]
    candidates = np.array(
        [
            1 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2],
            1 - rotation[0, 0] + rotation[1, 1] - rotation[2, 2],
            1 - rotation[0, 0] - rotation[1, 1] + rotation[2, 2],
            1 + np.trace(rotation),
        ]
    )
    index = int(np.argmax(candidates))
    root = np.sqrt(max(candidates[index], 0.0)) / 2
    if root < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0])
    denominator = 4 * root
    if index == 0:
        result = [
            root,
            (rotation[0, 1] + rotation[1, 0]) / denominator,
            (rotation[0, 2] + rotation[2, 0]) / denominator,
            (rotation[2, 1] - rotation[1, 2]) / denominator,
        ]
    elif index == 1:
        result = [
            (rotation[0, 1] + rotation[1, 0]) / denominator,
            root,
            (rotation[1, 2] + rotation[2, 1]) / denominator,
            (rotation[0, 2] - rotation[2, 0]) / denominator,
        ]
    elif index == 2:
        result = [
            (rotation[0, 2] + rotation[2, 0]) / denominator,
            (rotation[1, 2] + rotation[2, 1]) / denominator,
            root,
            (rotation[1, 0] - rotation[0, 1]) / denominator,
        ]
    else:
        result = [
            (rotation[2, 1] - rotation[1, 2]) / denominator,
            (rotation[0, 2] - rotation[2, 0]) / denominator,
            (rotation[1, 0] - rotation[0, 1]) / denominator,
            root,
        ]
    return normalize_quaternion(np.asarray(result))


def pose_matrix(
    position: NDArray[np.floating], quaternion_xyzw: NDArray[np.floating]
) -> FloatArray:
    transform = np.eye(4, dtype=np.float64)
    transform[:3, :3] = quaternion_to_matrix(quaternion_xyzw)
    transform[:3, 3] = np.asarray(position, dtype=np.float64)
    return transform


def invert_transform(transform: NDArray[np.floating]) -> FloatArray:
    value = np.asarray(transform, dtype=np.float64)
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = value[:3, :3].T
    result[:3, 3] = -result[:3, :3] @ value[:3, 3]
    return result


def marker_object_points(size_m: float) -> FloatArray:
    half = size_m / 2
    return np.array(
        [
            [-half, half, 0.0],
            [half, half, 0.0],
            [half, -half, 0.0],
            [-half, -half, 0.0],
        ],
        dtype=np.float64,
    )


def transform_points(transform: NDArray[np.floating], points: NDArray[np.floating]) -> FloatArray:
    points_array = np.asarray(points, dtype=np.float64)
    homogeneous = np.column_stack((points_array, np.ones(len(points_array))))
    return (np.asarray(transform) @ homogeneous.T).T[:, :3]


def slerp(left: NDArray[np.floating], right: NDArray[np.floating], amount: float) -> FloatArray:
    q0 = normalize_quaternion(left)
    q1 = normalize_quaternion(right)
    dot = float(np.dot(q0, q1))
    if dot < 0:
        q1 = -q1
        dot = -dot
    if dot > 0.9995:
        return normalize_quaternion(q0 + amount * (q1 - q0))
    angle = np.arccos(np.clip(dot, -1.0, 1.0))
    return (np.sin((1 - amount) * angle) * q0 + np.sin(amount * angle) * q1) / np.sin(angle)


def average_transforms(transforms: list[NDArray[np.floating]]) -> FloatArray:
    if not transforms:
        raise ValueError("at least one transform is required")
    translations = np.array([np.asarray(item)[:3, 3] for item in transforms])
    median = np.median(translations, axis=0)
    distances = np.linalg.norm(translations - median, axis=1)
    cutoff = max(
        float(np.median(distances) + 3 * np.median(np.abs(distances - np.median(distances)))), 1e-6
    )
    selected = [
        item for item, distance in zip(transforms, distances, strict=True) if distance <= cutoff
    ]
    quaternions = np.array([matrix_to_quaternion(item[:3, :3]) for item in selected])
    reference = quaternions[0]
    quaternions = np.array([q if np.dot(q, reference) >= 0 else -q for q in quaternions])
    accumulator = sum(np.outer(q, q) for q in quaternions)
    _, eigenvectors = np.linalg.eigh(accumulator)
    quaternion = normalize_quaternion(eigenvectors[:, -1])
    return pose_matrix(np.median([item[:3, 3] for item in selected], axis=0), quaternion)
