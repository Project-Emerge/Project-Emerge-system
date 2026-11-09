import json
from pathlib import Path

import cv2
import numpy as np

from vision_system.calibration.board import BoardSpec, create_charuco_board, generate_board
from vision_system.calibration.extrinsics import (
    estimate_world_from_camera,
    extrinsic_sample_diagnostic,
)
from vision_system.calibration.intrinsics import calibrate_intrinsics
from vision_system.calibration.samples import CalibrationSample
from vision_system.calibration.store import load_calibrations
from vision_system.core.config import CameraCalibration, ReferenceMarkerConfig, save_json
from vision_system.core.geometry import (
    invert_transform,
    marker_object_points,
    pose_matrix,
    transform_points,
)


def test_generate_a4_board(tmp_path: Path) -> None:
    pdf, png, metadata = generate_board(tmp_path)
    assert pdf.stat().st_size > 1_000
    assert png.stat().st_size > 1_000
    assert metadata.stat().st_size > 100
    image = cv2.imread(str(png))
    assert image is not None
    assert image.shape[1] / image.shape[0] == pytest_approx(0.75, 0.01)


def test_generate_a3_board(tmp_path: Path) -> None:
    spec = BoardSpec.for_format("a3")
    pdf, png, metadata = generate_board(tmp_path, spec)
    assert pdf.name == "charuco-a3-print.pdf"
    assert pdf.stat().st_size > 1_000
    image = cv2.imread(str(png))
    assert image is not None
    assert image.shape[1] / image.shape[0] == pytest_approx(280 / 360, 0.01)
    values = json.loads(metadata.read_text(encoding="utf-8"))
    assert values["page_format"] == "a3"
    assert values["squares_x"] == 7
    assert values["squares_y"] == 9
    assert values["square_length_m"] == pytest_approx(0.04, 1e-9)
    assert values["marker_length_m"] == pytest_approx(0.03, 1e-9)


def test_unknown_board_format_is_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="unsupported"):
        BoardSpec.for_format("letter")


def pytest_approx(value: float, tolerance: float):
    import pytest

    return pytest.approx(value, abs=tolerance)


def test_synthetic_intrinsic_calibration() -> None:
    board = create_charuco_board(BoardSpec())
    object_points = np.asarray(board.getChessboardCorners(), dtype=np.float32)
    identifiers = np.arange(len(object_points), dtype=np.int32).reshape(-1, 1)
    matrix = np.array([[1250.0, 0, 960], [0, 1230.0, 540], [0, 0, 1]])
    distortion = np.zeros(8)
    samples = []
    for index in range(20):
        rvec = np.array([0.03 * (index % 5 - 2), 0.04 * (index % 4 - 1), 0.02 * index])
        tvec = np.array([(index % 5 - 2) * 0.05, (index % 4 - 1) * 0.04, 0.7 + 0.03 * index])
        image_points, _ = cv2.projectPoints(object_points, rvec, tvec, matrix, distortion)
        samples.append(CalibrationSample(image_points.astype(np.float32), identifiers.copy()))
    recovered, _, median, p95 = calibrate_intrinsics(samples, (1920, 1080), board)
    assert median < 0.05
    assert p95 < 0.1
    assert abs(recovered[0, 0] - matrix[0, 0]) < 20


def test_synthetic_reference_extrinsic() -> None:
    matrix = np.array([[1200.0, 0, 960], [0, 1200.0, 540], [0, 0, 1]])
    distortion = np.zeros(8)
    references = {
        0: ReferenceMarkerConfig(
            id=0, size_m=0.15, position_m=(-0.3, 0, 2), orientation_xyzw=(0, 0, 0, 1)
        ),
        1: ReferenceMarkerConfig(
            id=1, size_m=0.15, position_m=(0.3, 0, 2), orientation_xyzw=(0, 0, 0, 1)
        ),
        2: ReferenceMarkerConfig(
            id=2, size_m=0.15, position_m=(0.0, 0.3, 2), orientation_xyzw=(0, 0, 0, 1)
        ),
    }
    expected_world_from_camera = pose_matrix(np.array([0.05, -0.02, 0.0]), np.array([0, 0, 0, 1]))
    camera_from_world = invert_transform(expected_world_from_camera)
    rotation, _ = cv2.Rodrigues(camera_from_world[:3, :3])
    corners = []
    for reference in references.values():
        world_from_tag = pose_matrix(
            np.asarray(reference.position_m), np.asarray(reference.orientation_xyzw)
        )
        world_points = transform_points(world_from_tag, marker_object_points(reference.size_m))
        projected, _ = cv2.projectPoints(
            world_points,
            rotation,
            camera_from_world[:3, 3],
            matrix,
            distortion,
        )
        corners.append(projected.astype(np.float32))
    estimate = estimate_world_from_camera(
        corners, np.array([[0], [1], [2]], dtype=np.int32), references, matrix, distortion
    )
    assert estimate is not None
    np.testing.assert_allclose(estimate[0], expected_world_from_camera, atol=1e-5)
    assert estimate[1] < 1e-3


def test_solvepnp_failure_returns_none(monkeypatch) -> None:
    """estimate_world_from_camera returns None when solvePnP fails."""

    def reject_pose(*args, **kwargs):
        return False, None, None

    monkeypatch.setattr(cv2, "solvePnP", reject_pose)
    references = {
        marker_id: ReferenceMarkerConfig(
            id=marker_id,
            size_m=0.15,
            position_m=(float(marker_id), 0, 2),
            orientation_xyzw=(0, 0, 0, 1),
        )
        for marker_id in (0, 1, 2)
    }
    corners = [np.zeros((1, 4, 2), dtype=np.float32) for _ in references]
    ids = np.array([[0], [1], [2]], dtype=np.int32)

    strict = estimate_world_from_camera(corners, ids, references, np.eye(3), np.zeros(8))
    relaxed = estimate_world_from_camera(
        corners,
        ids,
        references,
        np.eye(3),
        np.zeros(8),
        allow_low_quality=True,
    )

    assert strict is None
    assert relaxed is None


def test_load_calibrations_ignores_folder_reports(tmp_path: Path) -> None:
    calibration = CameraCalibration(
        camera_id="cam_0",
        source=5,
        image_size=(1920, 1080),
        camera_matrix=[[1200, 0, 960], [0, 1200, 540], [0, 0, 1]],
        distortion=[0] * 8,
        intrinsic_median_error_px=0.2,
        intrinsic_p95_error_px=0.4,
        captured_at="2026-08-17T12:00:00+00:00",
        opencv_version=cv2.__version__,
        board_checksum="test",
    )
    save_json(tmp_path / "cam_0.json", calibration)
    (tmp_path / "cam_0-folder-report.json").write_text(
        json.dumps({"quality_passed": False, "candidate_calibration": {}}), encoding="utf-8"
    )

    loaded = load_calibrations(tmp_path)

    assert list(loaded) == ["cam_0"]


def test_extrinsic_diagnostic_explains_that_target_does_not_count() -> None:
    accepted, detection, reason = extrinsic_sample_diagnostic(
        seen_ids=[0, 23], reference_ids={0, 1}, reprojection_error_px=None
    )

    assert not accepted
    assert "reference utili [0]" in detection
    assert "ignorati [23]" in detection
    assert "1/3" in reason
    assert "target" in reason


def test_extrinsic_diagnostic_reports_reprojection_rejection_and_override() -> None:
    rejected, _, reason = extrinsic_sample_diagnostic(
        seen_ids=[0, 1, 2], reference_ids={0, 1, 2}, reprojection_error_px=2.5
    )
    accepted, _, override_reason = extrinsic_sample_diagnostic(
        seen_ids=[0, 1, 2],
        reference_ids={0, 1, 2},
        reprojection_error_px=2.5,
        allow_low_quality=True,
    )

    assert not rejected
    assert "2.50px > 2.00px" in reason
    assert accepted
    assert "override" in override_reason
