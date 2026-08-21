"""JSON calibration artifact file loading and directory signature hashing."""

from __future__ import annotations

from pathlib import Path

from ..core.config import CameraCalibration

# Reports from the folder workflow live next to the calibrations but are not
# camera artifacts.
FOLDER_REPORT_SUFFIX = "-folder-report.json"


def _calibration_files(path: Path):
    return (
        file_path
        for file_path in path.glob("*.json")
        if not file_path.name.endswith(FOLDER_REPORT_SUFFIX)
    )


def load_calibrations(path: Path) -> dict[str, CameraCalibration]:
    calibrations: dict[str, CameraCalibration] = {}
    for file_path in _calibration_files(path):
        calibration = CameraCalibration.model_validate_json(file_path.read_text(encoding="utf-8"))
        calibrations[calibration.camera_id] = calibration
    return calibrations


def calibration_dir_signature(path: Path) -> tuple:
    """Fingerprint of the calibration artifacts, for change detection."""
    return tuple(
        sorted(
            (file_path.name, file_path.stat().st_mtime_ns, file_path.stat().st_size)
            for file_path in _calibration_files(path)
        )
    )
