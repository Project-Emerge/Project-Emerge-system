import numpy as np

from vision_system.core.geometry import (
    average_transforms,
    invert_transform,
    marker_object_points,
    matrix_to_quaternion,
    pose_matrix,
    quaternion_to_matrix,
    transform_points,
)


def test_quaternion_matrix_round_trip() -> None:
    quaternion = np.array([0.1, -0.2, 0.3, 0.9])
    quaternion /= np.linalg.norm(quaternion)
    recovered = matrix_to_quaternion(quaternion_to_matrix(quaternion))
    assert abs(float(np.dot(quaternion, recovered))) > 1 - 1e-10


def test_transform_inverse() -> None:
    transform = pose_matrix(np.array([1.0, 2.0, 3.0]), np.array([0.0, 0.0, 0.0, 1.0]))
    np.testing.assert_allclose(transform @ invert_transform(transform), np.eye(4), atol=1e-12)


def test_marker_corner_convention() -> None:
    points = marker_object_points(0.2)
    np.testing.assert_allclose(
        points,
        [[-0.1, 0.1, 0], [0.1, 0.1, 0], [0.1, -0.1, 0], [-0.1, -0.1, 0]],
    )


def test_transform_points_and_robust_average() -> None:
    transforms = [
        pose_matrix(np.array([1.0, 2.0, 0.0]), np.array([0, 0, 0, 1])),
        pose_matrix(np.array([1.01, 1.99, 0.0]), np.array([0, 0, 0, 1])),
        pose_matrix(np.array([50.0, 50.0, 0.0]), np.array([0, 0, 0, 1])),
    ]
    average = average_transforms(transforms)
    np.testing.assert_allclose(average[:2, 3], [1.005, 1.995], atol=0.02)
    transformed = transform_points(transforms[0], np.array([[0.5, 0.5, 0]]))
    np.testing.assert_allclose(transformed, [[1.5, 2.5, 0]])
