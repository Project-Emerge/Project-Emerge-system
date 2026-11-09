import json
from pathlib import Path

import cv2
import numpy as np

import vision_system.calibration.folder as offline
from vision_system.calibration.board import BoardSpec, create_charuco_board
from vision_system.calibration.samples import CalibrationSample, Coverage
from vision_system.core.config import CameraConfig


def test_collects_charuco_photos_from_folder(tmp_path: Path) -> None:
    spec = BoardSpec.for_format("a4")
    board_image = create_charuco_board(spec).generateImage((450, 600))
    for index, x in enumerate((100, 650)):
        photo = np.full((900, 1200), 255, dtype=np.uint8)
        photo[150:750, x : x + 450] = board_image
        assert cv2.imwrite(str(tmp_path / f"photo-{index}.JPG"), photo)
    samples, image_size, coverage, records = offline.collect_folder_samples(tmp_path, spec)
    assert image_size == (1200, 900)
    assert len(samples) == 2
    assert len(records) == 2
    assert all(record["accepted"] for record in records)
    assert int(coverage.grid.sum()) == 2


def synthetic_samples() -> list[CalibrationSample]:
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
    return samples


def test_low_coverage_requires_explicit_override(tmp_path: Path, monkeypatch) -> None:
    samples = synthetic_samples()
    records = [{"file": f"{index}.png", "accepted": True} for index in range(len(samples))]

    def fake_collection(*args, **kwargs):
        return samples, (1920, 1080), Coverage(), records

    monkeypatch.setattr(offline, "collect_folder_samples", fake_collection)
    camera = CameraConfig(id="cam_0", source=5)
    rejected = offline.calibrate_camera_folder(
        camera, tmp_path, tmp_path / "strict", BoardSpec(), allow_low_quality=False
    )
    assert not rejected.quality_passed
    assert not rejected.written
    assert rejected.calibration is not None
    assert not (tmp_path / "strict" / "cam_0.json").exists()
    strict_report = json.loads(rejected.report_path.read_text(encoding="utf-8"))
    assert strict_report["calibration_written"] is False

    overridden = offline.calibrate_camera_folder(
        camera, tmp_path, tmp_path / "override", BoardSpec(), allow_low_quality=True
    )
    assert overridden.written
    assert overridden.calibration_path is not None and overridden.calibration_path.exists()
    calibration = json.loads(overridden.calibration_path.read_text(encoding="utf-8"))
    assert calibration["camera_id"] == "cam_0"
    assert len(calibration["camera_matrix"]) == 3
    assert len(calibration["distortion"]) >= 8
