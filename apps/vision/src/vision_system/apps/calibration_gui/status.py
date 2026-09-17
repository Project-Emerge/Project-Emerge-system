"""What is calibrated, how well, and what still blocks the next step.

Pure: it takes an already-loaded configuration and an already-loaded set of
artifacts and returns value objects. No disk, no clock, no OpenCV — which is what
makes the table the panel draws trivial to test.

Every predicate here is borrowed rather than re-derived. In particular "stale"
means ``calibration_compatibility_error``, the same gate ``vision-calibrate``
enforces before extrinsics, so the panel's badge and the CLI's refusal cannot
drift apart.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...calibration.board import BoardSpec, board_checksum
from ...calibration.intrinsics import intrinsic_quality_passed
from ...core.config import AppConfig, CameraCalibration, CameraConfig
from ...pipeline.calibration_store import calibration_compatibility_error

Stage = Literal["intrinsics", "extrinsics"]

# Extrinsics needs at least this many reference markers to solve a pose.
MIN_REFERENCES_FOR_EXTRINSICS = 3


@dataclass(frozen=True)
class CameraStatus:
    """One row of the calibration table."""

    camera_id: str
    source: int | str
    has_intrinsics: bool
    intrinsic_median_error_px: float | None = None
    intrinsic_p95_error_px: float | None = None
    intrinsic_quality_passed: bool | None = None
    has_extrinsics: bool = False
    extrinsic_median_error_px: float | None = None
    extrinsic_p95_error_px: float | None = None
    extrinsic_quality_passed: bool | None = None
    board_matches: bool | None = None
    geometry_error: str | None = None
    source_matches: bool = True
    captured_at: str | None = None
    artifact_path: Path | None = None

    @property
    def stale(self) -> bool:
        """The artifact exists but no longer describes this camera."""
        return self.geometry_error is not None or self.board_matches is False

    def complete(self, stage: Stage) -> bool:
        """Usable for the given stage: present, good enough, and still valid."""
        if self.stale:
            return False
        if stage == "intrinsics":
            return self.has_intrinsics and self.intrinsic_quality_passed is not False
        return self.has_extrinsics and self.extrinsic_quality_passed is not False


@dataclass(frozen=True)
class CalibrationOverview:
    """The whole roster's calibration state, plus the context it was read in."""

    cameras: tuple[CameraStatus, ...]
    calibrations_dir: Path
    config_path: Path | None = None
    config_revision: int = 0
    board_format: str = "a4"
    reference_marker_count: int = 0

    def camera_ids(self) -> tuple[str, ...]:
        return tuple(camera.camera_id for camera in self.cameras)

    def status(self, camera_id: str) -> CameraStatus | None:
        return next((c for c in self.cameras if c.camera_id == camera_id), None)

    def missing(self, stage: Stage) -> tuple[str, ...]:
        """Cameras that cannot move past this stage yet."""
        return tuple(c.camera_id for c in self.cameras if not c.complete(stage))

    def stale_cameras(self) -> tuple[str, ...]:
        return tuple(c.camera_id for c in self.cameras if c.stale)

    def complete(self, stage: Stage) -> bool:
        return bool(self.cameras) and not self.missing(stage)

    def has_enough_references(self) -> bool:
        return self.reference_marker_count >= MIN_REFERENCES_FOR_EXTRINSICS


def describe_camera(
    camera: CameraConfig,
    calibration: CameraCalibration | None,
    *,
    expected_board_checksum: str | None,
    calibrations_dir: Path,
) -> CameraStatus:
    """Join one configured camera with its stored artifact, if any."""
    if calibration is None:
        return CameraStatus(
            camera_id=camera.id, source=camera.source, has_intrinsics=False
        )
    return CameraStatus(
        camera_id=camera.id,
        source=camera.source,
        has_intrinsics=True,
        intrinsic_median_error_px=calibration.intrinsic_median_error_px,
        intrinsic_p95_error_px=calibration.intrinsic_p95_error_px,
        intrinsic_quality_passed=intrinsic_quality_passed(
            calibration.intrinsic_median_error_px, calibration.intrinsic_p95_error_px
        ),
        has_extrinsics=calibration.world_from_camera is not None,
        extrinsic_median_error_px=calibration.extrinsic_median_error_px,
        extrinsic_p95_error_px=calibration.extrinsic_p95_error_px,
        extrinsic_quality_passed=calibration.extrinsic_quality_passed,
        board_matches=(
            None
            if expected_board_checksum is None
            else calibration.board_checksum == expected_board_checksum
        ),
        geometry_error=calibration_compatibility_error(camera, calibration),
        source_matches=camera.source == calibration.source,
        captured_at=calibration.captured_at,
        artifact_path=calibrations_dir / f"{camera.id}.json",
    )


def describe_calibrations(
    config: AppConfig,
    calibrations: Mapping[str, CameraCalibration],
    *,
    calibrations_dir: Path,
    config_path: Path | None = None,
    board_spec: BoardSpec | None = None,
) -> CalibrationOverview:
    """Build the table for the configured roster — two, three or four cameras."""
    spec = board_spec or BoardSpec()
    expected = board_checksum(spec)
    return CalibrationOverview(
        cameras=tuple(
            describe_camera(
                camera,
                calibrations.get(camera.id),
                expected_board_checksum=expected,
                calibrations_dir=calibrations_dir,
            )
            for camera in config.cameras
        ),
        calibrations_dir=calibrations_dir,
        config_path=config_path,
        config_revision=config.revision,
        board_format=spec.page_format,
        reference_marker_count=len(config.aruco.reference_markers),
    )
