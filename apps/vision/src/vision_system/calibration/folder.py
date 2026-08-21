"""Batch intrinsic calibration from folders of pre-captured ChArUco photos."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from ..core.config import AppConfig, CameraCalibration, CameraConfig, load_config, save_json
from ..pipeline.capture import apply_digital_zoom
from .board import BoardSpec, board_checksum, create_charuco_board, create_charuco_detector
from .intrinsics import calibrate_intrinsics, intrinsic_quality_passed
from .samples import (
    DEFAULT_SHARPNESS_THRESHOLD,
    FOLDER_POSE_NOVELTY_THRESHOLD,
    MIN_CHARUCO_CORNERS,
    CalibrationSample,
    Coverage,
    board_within_margins,
    frame_sharpness,
    sample_signature,
    signature_distance,
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


@dataclass
class FolderCalibrationResult:
    camera_id: str
    calibration: CameraCalibration | None
    calibration_path: Path | None
    report_path: Path
    quality_passed: bool
    written: bool


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise NotADirectoryError(folder)
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def collect_folder_samples(
    folder: Path,
    spec: BoardSpec,
    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
    min_corners: int = MIN_CHARUCO_CORNERS,
    digital_zoom: float = 1.0,
) -> tuple[list[CalibrationSample], tuple[int, int] | None, Coverage, list[dict]]:
    board = create_charuco_board(spec)
    detector = create_charuco_detector(board)
    samples: list[CalibrationSample] = []
    coverage = Coverage()
    records: list[dict] = []
    image_size: tuple[int, int] | None = None
    signatures: list[np.ndarray] = []
    for path in image_files(folder):
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        record: dict = {"file": path.name, "accepted": False}
        if image is None:
            record["reason"] = "unreadable_image"
            records.append(record)
            continue
        image = apply_digital_zoom(image, digital_zoom)
        height, width = image.shape[:2]
        current_size = (width, height)
        if image_size is None:
            image_size = current_size
        elif current_size != image_size:
            record.update(
                reason="resolution_mismatch",
                image_size=list(current_size),
                expected_size=list(image_size),
            )
            records.append(record)
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        corners, ids, marker_corners, marker_ids = detector.detectBoard(gray)
        corner_count = 0 if ids is None else len(ids)
        sharpness = frame_sharpness(gray)
        record.update(corners=corner_count, sharpness=round(sharpness, 3))
        if ids is None or corner_count < min_corners:
            record["reason"] = "not_enough_charuco_corners"
            records.append(record)
            continue
        signature = sample_signature(board, corners, ids, current_size)
        if signature is None:
            record["reason"] = "board_pose_unavailable"
            records.append(record)
            continue
        if not board_within_margins(corners.reshape(-1, 2), width, height):
            record["reason"] = "board_too_close_to_image_border"
            records.append(record)
            continue
        if sharpness < sharpness_threshold:
            record["reason"] = "blurred_image"
            records.append(record)
            continue
        if signatures and min(
            signature_distance(signature, previous) for previous in signatures
        ) < FOLDER_POSE_NOVELTY_THRESHOLD:
            record["reason"] = "duplicate_board_pose"
            records.append(record)
            continue
        sample = CalibrationSample(
            corners=np.asarray(corners, dtype=np.float32),
            ids=np.asarray(ids, dtype=np.int32),
            image_path=path,
            signature=signature,
        )
        samples.append(sample)
        signatures.append(signature)
        coverage.register(signature)
        record.update(accepted=True, reason="accepted", signature=signature.tolist())
        records.append(record)
    return samples, image_size, coverage, records


def _coverage_payload(coverage: Coverage, sample_count: int) -> dict:
    return {
        "complete": coverage.complete(sample_count),
        "grid": coverage.grid.tolist(),
        "scales": coverage.scales,
        "tilts": coverage.tilts,
    }


def calibrate_camera_folder(
    camera: CameraConfig,
    folder: Path,
    output_dir: Path,
    spec: BoardSpec,
    allow_low_quality: bool = False,
    sharpness_threshold: float = 80.0,
) -> FolderCalibrationResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{camera.id}-folder-report.json"
    samples, image_size, coverage, records = collect_folder_samples(
        folder, spec, sharpness_threshold, digital_zoom=camera.digital_zoom
    )
    report: dict = {
        "schema_version": 1,
        "camera_id": camera.id,
        "source": camera.source,
        "digital_zoom": camera.digital_zoom,
        "input_folder": str(folder.resolve()),
        "board": spec.__dict__,
        "images_total": len(records),
        "images_accepted": len(samples),
        "images_rejected": len(records) - len(samples),
        "image_size": list(image_size) if image_size else None,
        "coverage": _coverage_payload(coverage, len(samples)),
        "images": records,
        "allow_low_quality": allow_low_quality,
    }
    if image_size is None or len(samples) < 10:
        report.update(
            quality_passed=False,
            calibration_written=False,
            error=f"at least 10 valid photos are required; found {len(samples)}",
        )
        save_json(report_path, report)
        return FolderCalibrationResult(camera.id, None, None, report_path, False, False)

    board = create_charuco_board(spec)
    matrix, distortion, median_error, p95_error = calibrate_intrinsics(samples, image_size, board)
    quality_passed = coverage.complete(len(samples)) and intrinsic_quality_passed(
        median_error, p95_error
    )
    calibration = CameraCalibration(
        camera_id=camera.id,
        source=camera.source,
        image_size=image_size,
        camera_zoom=camera.zoom,
        camera_digital_zoom=camera.digital_zoom,
        camera_matrix=matrix.tolist(),
        distortion=distortion.tolist(),
        intrinsic_median_error_px=median_error,
        intrinsic_p95_error_px=p95_error,
        captured_at=datetime.now(UTC).isoformat(),
        opencv_version=cv2.__version__,
        board_checksum=board_checksum(spec),
    )
    calibration_path = output_dir / f"{camera.id}.json"
    written = quality_passed or allow_low_quality
    if written:
        save_json(calibration_path, calibration)
    report.update(
        quality_passed=quality_passed,
        calibration_written=written,
        intrinsic_median_error_px=median_error,
        intrinsic_p95_error_px=p95_error,
        candidate_calibration=calibration.model_dump(mode="json"),
        error=None if quality_passed else "quality thresholds failed; inspect coverage and errors",
    )
    save_json(report_path, report)
    return FolderCalibrationResult(
        camera.id,
        calibration,
        calibration_path if written else None,
        report_path,
        quality_passed,
        written,
    )


def calibrate_folder_tree(
    config: AppConfig,
    input_path: Path,
    output_dir: Path,
    board_format: str = "a4",
    camera_id: str | None = None,
    allow_low_quality: bool = False,
    sharpness_threshold: float = 80.0,
) -> list[FolderCalibrationResult]:
    spec = BoardSpec.for_format(board_format)
    cameras = {camera.id: camera for camera in config.cameras}
    if camera_id:
        if camera_id not in cameras:
            raise ValueError(f"unknown camera id: {camera_id}")
        jobs = [(cameras[camera_id], input_path)]
    else:
        jobs = [
            (camera, input_path / camera.id)
            for camera in config.cameras
            if (input_path / camera.id).is_dir()
        ]
        if not jobs:
            expected = ", ".join(camera.id for camera in config.cameras)
            raise ValueError(f"no camera subfolders found; expected one or more of: {expected}")
    return [
        calibrate_camera_folder(
            camera,
            folder,
            output_dir,
            spec,
            allow_low_quality,
            sharpness_threshold,
        )
        for camera, folder in jobs
    ]


def print_results(results: list[FolderCalibrationResult]) -> None:
    for result in results:
        print(f"\n{result.camera_id}")
        print(f"  report: {result.report_path}")
        print(f"  quality: {'PASS' if result.quality_passed else 'FAIL'}")
        if result.calibration:
            print("  camera_matrix:")
            print(np.array2string(np.asarray(result.calibration.camera_matrix), precision=8))
            print("  distortion:")
            print(np.array2string(np.asarray(result.calibration.distortion), precision=8))
        if result.calibration_path:
            print(f"  calibration: {result.calibration_path}")
        else:
            print("  calibration: not written")


def folder_calibration_main() -> None:
    parser = argparse.ArgumentParser(description="Calibra una o quattro camere da foto ChArUco")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--camera", help="camera singola; senza usa sottocartelle cam_0..cam_3")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("calibrations"))
    parser.add_argument("--board-format", choices=("a4", "a3"), default="a4")
    parser.add_argument("--sharpness-threshold", type=float, default=80.0)
    parser.add_argument("--allow-low-quality", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config) if args.config else AppConfig()
    try:
        results = calibrate_folder_tree(
            config,
            args.input,
            args.output,
            args.board_format,
            args.camera,
            args.allow_low_quality,
            args.sharpness_threshold,
        )
    except (ValueError, NotADirectoryError) as error:
        parser.error(str(error))
    print_results(results)
    if not all(result.written for result in results):
        raise SystemExit(2)
