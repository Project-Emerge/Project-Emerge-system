"""Interactive guided OpenCV wizards for intrinsic and extrinsic calibration."""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.aruco import create_subpixel_detector
from ..core.config import AppConfig, CameraCalibration, CameraConfig, save_json
from ..core.geometry import average_transforms
from ..pipeline.capture import apply_camera_properties, apply_digital_zoom, open_video_capture
from ..transport.diagnostics import event
from .board import BoardSpec, board_checksum, create_charuco_board, create_charuco_detector
from .extrinsics import (
    MAX_SAMPLE_REPROJECTION_ERROR_PX,
    MIN_REFERENCES_FOR_EXTRINSICS,
    estimate_world_from_camera,
    extrinsic_sample_diagnostic,
    extrinsic_sample_outcome,
    ransac_threshold_px,
)
from .intrinsics import (
    INTRINSIC_MAX_MEDIAN_ERROR_PX,
    INTRINSIC_MAX_P95_ERROR_PX,
    calibrate_intrinsics,
    intrinsic_quality_passed,
)
from .samples import (
    DEFAULT_SHARPNESS_THRESHOLD,
    LIVE_POSE_NOVELTY_THRESHOLD,
    MIN_CHARUCO_CORNERS,
    MIN_COVERAGE_SAMPLES,
    CalibrationSample,
    Coverage,
    board_within_margins,
    frame_sharpness,
    is_novel_signature,
    sample_signature,
)

LOGGER = logging.getLogger(__name__)

# Intrinsic wizard: a frame is captured once the board stays still for
# STABILITY_DWELL_S and at least LIVE_CAPTURE_COOLDOWN_S passed since the
# previous capture.
STABILITY_CENTROID_TOLERANCE = 0.01
STABILITY_DWELL_S = 0.4
LIVE_CAPTURE_COOLDOWN_S = 0.9

# Extrinsic wizard: collection stops at TARGET_EXTRINSIC_SAMPLES accepted
# frames; progress is logged every PROGRESS_LOG_PERIOD_S.
TARGET_EXTRINSIC_SAMPLES = 100
PROGRESS_LOG_PERIOD_S = 2.0
STABILITY_PERCENTILE = 95.0

INTRINSIC_WINDOW_TITLE = "Calibrazione intrinseca - {camera_id}"
EXTRINSIC_WINDOW_TITLE = "Calibrazione estrinseca - {camera_id}"

KEY_ESC = 27
KEY_SPACE = ord(" ")
KEY_RESET = ord("r")
KEY_QUIT = ord("q")
UNDO_KEYS = (8, 127)
CONFIRM_KEYS = (10, 13)


def _close_wizard(capture, window: str) -> None:
    capture.release()
    try:
        cv2.destroyWindow(window)
    except cv2.error:
        # The window may already be gone after Ctrl-C or a GUI shutdown.
        pass


def _draw_lines(image: NDArray[np.uint8], lines: list[str], good: bool = False) -> None:
    overlay = image.copy()
    panel_height = min(image.shape[0], 18 + len(lines) * 27)
    cv2.rectangle(overlay, (0, 0), (image.shape[1], panel_height), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.65, image, 0.35, 0, image)
    color = (80, 255, 80) if good else (255, 255, 255)
    for index, line in enumerate(lines):
        cv2.putText(image, line, (20, 30 + index * 27), cv2.FONT_HERSHEY_SIMPLEX, 0.68, color, 2)


@dataclass
class IntrinsicCapturePolicy:
    """Decides, frame by frame, whether the live board view is worth capturing.

    Pure state machine with no camera or GUI dependency: the wizard feeds it
    detections and timestamps, tests can drive it headless.
    """

    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD
    stable_since: float | None = None
    last_centroid: NDArray[np.float64] | None = None
    last_capture: float = 0.0

    def evaluate(
        self,
        signature: NDArray[np.float64] | None,
        charuco_corners,
        charuco_count: int,
        sharpness: float,
        image_size: tuple[int, int],
        coverage: Coverage,
        captured_signatures: list[NDArray[np.float64]],
        now: float,
    ) -> tuple[str, bool]:
        """Return the operator instruction and whether the frame is capturable."""
        if charuco_count < MIN_CHARUCO_CORNERS:
            return (
                f"Pochi corner ({charuco_count}/{MIN_CHARUCO_CORNERS}): "
                "avvicina o migliora la luce",
                False,
            )
        if signature is None:
            return "Board non stimabile: rendila completamente visibile", False
        width, height = image_size
        points = charuco_corners.reshape(-1, 2)
        if not board_within_margins(points, width, height):
            return "Board troppo vicina al bordo: spostala verso il centro", False
        if sharpness < self.sharpness_threshold:
            return f"Immagine mossa ({sharpness:.0f}): tieni ferma la board", False
        x, y, _, _, _ = signature
        centroid = np.array([x, y])
        moved = (
            self.last_centroid is None
            or np.linalg.norm(centroid - self.last_centroid)
            > STABILITY_CENTROID_TOLERANCE
        )
        if moved:
            self.stable_since = now
        self.last_centroid = centroid
        if self.stable_since is None or now - self.stable_since < STABILITY_DWELL_S:
            return f"{coverage.instruction()} | tieni ferma la board", False
        if not is_novel_signature(signature, captured_signatures, LIVE_POSE_NOVELTY_THRESHOLD):
            return "Posa gia acquisita: " + coverage.instruction(), False
        return "Posa valida: resta fermo", True

    def can_capture(self, ready: bool, signature: NDArray[np.float64] | None, now: float) -> bool:
        return ready and signature is not None and now - self.last_capture > LIVE_CAPTURE_COOLDOWN_S

    def mark_captured(self, now: float) -> None:
        self.last_capture = now
        self.stable_since = None


def store_intrinsic_sample(
    session: Path,
    frame: NDArray[np.uint8],
    charuco_corners,
    charuco_ids,
    signature: NDArray[np.float64],
    index: int,
) -> CalibrationSample:
    """Persist one captured board view as PNG frame plus NPZ sidecar."""
    image_path = session / f"frame-{index:03d}.png"
    cv2.imwrite(str(image_path), frame)
    sample = CalibrationSample(
        corners=np.asarray(charuco_corners, dtype=np.float32),
        ids=np.asarray(charuco_ids, dtype=np.int32),
        image_path=image_path,
        signature=signature,
    )
    np.savez_compressed(
        session / f"frame-{index:03d}.npz",
        corners=sample.corners,
        ids=sample.ids,
        signature=signature,
    )
    return sample


def build_intrinsic_calibration(
    camera: CameraConfig,
    samples: list[CalibrationSample],
    image_size: tuple[int, int],
    board,
    spec: BoardSpec,
) -> CameraCalibration:
    matrix, distortion, median_error, p95_error = calibrate_intrinsics(samples, image_size, board)
    return CameraCalibration(
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


def run_intrinsic_wizard(
    camera: CameraConfig,
    output_root: Path,
    spec: BoardSpec | None = None,
    sharpness_threshold: float = DEFAULT_SHARPNESS_THRESHOLD,
) -> CameraCalibration | None:
    """Interactive guided intrinsic calibration; returns None when cancelled."""
    spec = spec or BoardSpec()
    board = create_charuco_board(spec)
    detector = create_charuco_detector(board)
    policy = IntrinsicCapturePolicy(sharpness_threshold=sharpness_threshold)
    source = camera.source
    capture = open_video_capture(source)
    apply_camera_properties(capture, camera)
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {camera.id} from {source}")
    session = output_root / "sessions" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") / camera.id
    session.mkdir(parents=True, exist_ok=True)
    samples: list[CalibrationSample] = []
    coverage = Coverage()
    automatic = True
    result: CameraCalibration | None = None
    feedback: str | None = None
    override_pending = False
    window = INTRINSIC_WINDOW_TITLE.format(camera_id=camera.id)
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"frame capture failed for {camera.id}")
            frame = apply_digital_zoom(frame, camera.digital_zoom)
            height, width = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            charuco_corners, charuco_ids, marker_corners, marker_ids = detector.detectBoard(gray)
            display = frame.copy()
            signature = None
            if marker_ids is not None:
                cv2.aruco.drawDetectedMarkers(display, marker_corners, marker_ids)
            charuco_count = 0
            if charuco_ids is not None and len(charuco_ids):
                cv2.aruco.drawDetectedCornersCharuco(display, charuco_corners, charuco_ids)
                signature = sample_signature(board, charuco_corners, charuco_ids, (width, height))
                charuco_count = len(charuco_ids)
            captured_signatures = [
                sample.signature for sample in samples if sample.signature is not None
            ]
            reason, ready = policy.evaluate(
                signature,
                charuco_corners,
                charuco_count,
                frame_sharpness(gray),
                (width, height),
                coverage,
                captured_signatures,
                time.monotonic(),
            )
            now = time.monotonic()
            if automatic and policy.can_capture(ready, signature, now):
                sample = store_intrinsic_sample(
                    session, frame, charuco_corners, charuco_ids, signature, len(samples)
                )
                samples.append(sample)
                coverage.register(signature)
                policy.mark_captured(now)
                reason = "Immagine acquisita. " + coverage.instruction()
                if coverage.complete(len(samples)):
                    feedback = None
                    override_pending = False
                    result = build_intrinsic_calibration(
                        camera, samples, (width, height), board, spec
                    )
            quality_ok = result is not None and intrinsic_quality_passed(
                result.intrinsic_median_error_px, result.intrinsic_p95_error_px
            )
            scale_status = "/".join(
                str(coverage.scales[name]) for name in ("far", "medium", "near")
            )
            status = [
                f"{camera.id} | auto: {'ON' if automatic else 'OFF'} | "
                f"immagini: {len(samples)}/{MIN_COVERAGE_SAMPLES}",
                reason,
                f"scale L/M/V: {scale_status}",
                "SPAZIO auto | BACKSPACE annulla | R reset | ENTER conferma | ESC esci",
            ]
            if result is not None:
                status[1] = (
                    f"Errore validazione mediana {result.intrinsic_median_error_px:.2f}px, "
                    f"P95 {result.intrinsic_p95_error_px:.2f}px"
                )
                display = cv2.undistort(
                    display,
                    np.asarray(result.camera_matrix),
                    np.asarray(result.distortion),
                )
                status[2] = "ANTEPRIMA UNDISTORTA | verifica che le linee rette restino dritte"
            if feedback:
                status.append(feedback)
            _draw_lines(display, status, quality_ok or override_pending)
            cv2.imshow(window, display)
            key = cv2.waitKey(1) & 0xFF
            if key in (KEY_ESC, KEY_QUIT):
                return None
            if key == KEY_SPACE:
                automatic = not automatic
            elif key in UNDO_KEYS and samples:
                removed = samples.pop()
                if removed.image_path:
                    removed.image_path.unlink(missing_ok=True)
                    removed.image_path.with_suffix(".npz").unlink(missing_ok=True)
                coverage.rebuild(samples)
                result = None
                feedback = None
                override_pending = False
            elif key == KEY_RESET:
                samples.clear()
                coverage.rebuild(samples)
                result = None
                feedback = None
                override_pending = False
            elif key in CONFIRM_KEYS and result is not None:
                if quality_ok or override_pending:
                    save_json(output_root / f"{camera.id}.json", result)
                    return result
                override_pending = True
                feedback = (
                    f"Qualita insufficiente: mediana {result.intrinsic_median_error_px:.2f}px / "
                    f"P95 {result.intrinsic_p95_error_px:.2f}px (soglie "
                    f"{INTRINSIC_MAX_MEDIAN_ERROR_PX:.2f}/{INTRINSIC_MAX_P95_ERROR_PX:.2f}px). "
                    "Premi di nuovo ENTER per salvare comunque, BACKSPACE per togliere le "
                    "pose peggiori oppure R per ricominciare"
                )
    finally:
        _close_wizard(capture, window)


@dataclass(frozen=True)
class ExtrinsicQualityGates:
    """Final stability/reprojection gates for an extrinsic calibration."""

    translation_stability_p95_m: float = 0.01
    rotation_stability_p95_deg: float = 1.0
    median_error_px: float = 1.0
    p95_error_px: float = 2.0

    def as_dict(self) -> dict[str, float]:
        return {
            "translation_stability_p95_m": self.translation_stability_p95_m,
            "rotation_stability_p95_deg": self.rotation_stability_p95_deg,
            "median_error_px": self.median_error_px,
            "p95_error_px": self.p95_error_px,
        }


@dataclass(frozen=True)
class ExtrinsicQualityReport:
    world_from_camera: NDArray[np.float64]
    translation_stability_p95_m: float
    rotation_stability_p95_deg: float
    median_error_px: float
    p95_error_px: float

    def passed(self, gates: ExtrinsicQualityGates) -> bool:
        return not (
            self.translation_stability_p95_m > gates.translation_stability_p95_m
            or self.rotation_stability_p95_deg > gates.rotation_stability_p95_deg
            or self.median_error_px > gates.median_error_px
            or self.p95_error_px > gates.p95_error_px
        )


def evaluate_extrinsic_quality(
    transforms: list[NDArray[np.float64]], errors: list[float]
) -> ExtrinsicQualityReport:
    """Stability and reprojection statistics over the given accepted samples."""
    world_from_camera = average_transforms(transforms)
    translations = np.array([item[:3, 3] for item in transforms])
    translation_stability = float(
        np.percentile(
            np.linalg.norm(translations - np.median(translations, axis=0), axis=1),
            STABILITY_PERCENTILE,
        )
    )
    average_rotation = world_from_camera[:3, :3]
    rotation_residuals = []
    for transform in transforms:
        delta = average_rotation.T @ transform[:3, :3]
        rotation_residuals.append(
            np.degrees(np.arccos(np.clip((np.trace(delta) - 1) / 2, -1.0, 1.0)))
        )
    return ExtrinsicQualityReport(
        world_from_camera=world_from_camera,
        translation_stability_p95_m=translation_stability,
        rotation_stability_p95_deg=float(np.percentile(rotation_residuals, STABILITY_PERCENTILE)),
        median_error_px=float(np.median(errors)),
        p95_error_px=float(np.percentile(errors, STABILITY_PERCENTILE)),
    )


def run_extrinsic_wizard(
    camera: CameraConfig,
    calibration: CameraCalibration,
    config: AppConfig,
    output_root: Path,
    allow_low_quality: bool = False,
) -> CameraCalibration | None:
    """Interactive extrinsic calibration over live reference observations."""
    references = config.references_by_id()
    if len(references) < MIN_REFERENCES_FOR_EXTRINSICS:
        raise ValueError(
            f"at least {MIN_REFERENCES_FOR_EXTRINSICS} configured reference markers are required"
        )
    detector = create_subpixel_detector(config.aruco.dictionary)
    matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
    distortion = np.asarray(calibration.distortion, dtype=np.float64)
    ransac_error_px = ransac_threshold_px(allow_low_quality)
    gates = ExtrinsicQualityGates()
    capture = open_video_capture(camera.source)
    accepted_controls = apply_camera_properties(capture, camera)
    if not capture.isOpened():
        event(
            LOGGER,
            "extrinsic_camera_open_failed",
            level=logging.ERROR,
            camera_id=camera.id,
            source=camera.source,
        )
        raise RuntimeError(f"cannot open {camera.id}")
    event(
        LOGGER,
        "extrinsic_collection_started",
        camera_id=camera.id,
        source=camera.source,
        configured_size=[camera.width, camera.height],
        calibration_size=calibration.image_size,
        digital_zoom=camera.digital_zoom,
        calibrated_digital_zoom=calibration.camera_digital_zoom,
        accepted_controls=accepted_controls,
        ransac_error_px=ransac_error_px,
        final_sample_error_limit_px=MAX_SAMPLE_REPROJECTION_ERROR_PX,
        sample_target=TARGET_EXTRINSIC_SAMPLES,
        references={
            marker_id: {
                "size_m": reference.size_m,
                "position_m": reference.position_m,
                "orientation_xyzw": reference.orientation_xyzw,
            }
            for marker_id, reference in references.items()
        },
    )
    transforms: list[NDArray[np.float64]] = []
    errors: list[float] = []
    frame_count = 0
    outcome_counts: Counter[str] = Counter()
    seen_counts: Counter[int] = Counter()
    last_summary = time.monotonic()
    window = EXTRINSIC_WINDOW_TITLE.format(camera_id=camera.id)
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(f"frame capture failed for {camera.id}")
            frame = apply_digital_zoom(frame, camera.digital_zoom)
            corners, ids, rejected = detector.detectMarkers(frame)
            frame_count += 1
            display = frame.copy()
            if ids is not None:
                cv2.aruco.drawDetectedMarkers(display, corners, ids)
            seen = sorted(set(int(item) for item in ids.reshape(-1))) if ids is not None else []
            estimate = estimate_world_from_camera(
                corners,
                ids,
                references,
                matrix,
                distortion,
                allow_low_quality=allow_low_quality,
            )
            transform = estimate[0] if estimate is not None else None
            error = estimate[1] if estimate is not None else None
            accepted, detection_line, diagnostic = extrinsic_sample_diagnostic(
                seen,
                set(references),
                error,
                allow_low_quality,
                ransac_error_px,
            )
            seen_counts.update(seen)
            outcome_counts[
                extrinsic_sample_outcome(seen, set(references), error, allow_low_quality)
            ] += 1
            if accepted and transform is not None and error is not None:
                transforms.append(transform)
                errors.append(error)
            now = time.monotonic()
            if now - last_summary >= PROGRESS_LOG_PERIOD_S:
                recent_errors = errors[-TARGET_EXTRINSIC_SAMPLES:]
                event(
                    LOGGER,
                    "extrinsic_collection_progress",
                    camera_id=camera.id,
                    frames_processed=frame_count,
                    valid_samples=len(transforms),
                    outcome_counts=dict(outcome_counts),
                    marker_frame_counts=dict(seen_counts),
                    latest_seen_ids=seen,
                    latest_reprojection_error_px=error,
                    accepted_error_median_px=(
                        float(np.median(recent_errors)) if recent_errors else None
                    ),
                    accepted_error_p95_px=(
                        float(np.percentile(recent_errors, STABILITY_PERCENTILE))
                        if recent_errors
                        else None
                    ),
                    rejected_candidates=len(rejected),
                )
                last_summary = now
            complete = len(transforms) >= TARGET_EXTRINSIC_SAMPLES
            lines = [
                f"{camera.id} | campioni validi "
                f"{min(len(transforms), TARGET_EXTRINSIC_SAMPLES)}/{TARGET_EXTRINSIC_SAMPLES}",
                detection_line,
                f"Reference configurati: {sorted(references)}",
                diagnostic,
                *(
                    [f"ATTENZIONE: override qualita attivo | RANSAC {ransac_error_px:.0f}px"]
                    if allow_low_quality
                    else []
                ),
                "ENTER conferma quando verde | R reset | ESC esci",
            ]
            _draw_lines(display, lines, complete)
            cv2.imshow(window, display)
            key = cv2.waitKey(1) & 0xFF
            if key in (KEY_ESC, KEY_QUIT):
                event(
                    LOGGER,
                    "extrinsic_collection_cancelled",
                    camera_id=camera.id,
                    frames_processed=frame_count,
                    valid_samples=len(transforms),
                    outcome_counts=dict(outcome_counts),
                    marker_frame_counts=dict(seen_counts),
                )
                return None
            if key == KEY_RESET:
                event(
                    LOGGER,
                    "extrinsic_collection_reset",
                    camera_id=camera.id,
                    discarded_valid_samples=len(transforms),
                    outcome_counts=dict(outcome_counts),
                )
                transforms.clear()
                errors.clear()
            if key in CONFIRM_KEYS and complete:
                report = evaluate_extrinsic_quality(
                    transforms[-TARGET_EXTRINSIC_SAMPLES:],
                    errors[-TARGET_EXTRINSIC_SAMPLES:],
                )
                quality_passed = report.passed(gates)
                if not quality_passed and not allow_low_quality:
                    event(
                        LOGGER,
                        "extrinsic_quality_rejected",
                        level=logging.WARNING,
                        camera_id=camera.id,
                        translation_stability_p95_m=report.translation_stability_p95_m,
                        rotation_stability_p95_deg=report.rotation_stability_p95_deg,
                        median_error_px=report.median_error_px,
                        p95_error_px=report.p95_error_px,
                        limits=gates.as_dict(),
                        outcome_counts=dict(outcome_counts),
                    )
                    transforms.clear()
                    errors.clear()
                    continue
                updated = calibration.model_copy(
                    update={
                        "world_from_camera": report.world_from_camera.tolist(),
                        "extrinsic_median_error_px": report.median_error_px,
                        "extrinsic_p95_error_px": report.p95_error_px,
                        "extrinsic_quality_passed": quality_passed,
                        "captured_at": datetime.now(UTC).isoformat(),
                    }
                )
                save_json(output_root / f"{camera.id}.json", updated)
                event(
                    LOGGER,
                    "extrinsic_calibration_written",
                    camera_id=camera.id,
                    output=output_root / f"{camera.id}.json",
                    frames_processed=frame_count,
                    outcome_counts=dict(outcome_counts),
                    marker_frame_counts=dict(seen_counts),
                    translation_stability_p95_m=report.translation_stability_p95_m,
                    rotation_stability_p95_deg=report.rotation_stability_p95_deg,
                    median_error_px=report.median_error_px,
                    p95_error_px=report.p95_error_px,
                    quality_passed=quality_passed,
                    allow_low_quality=allow_low_quality,
                    world_from_camera=report.world_from_camera.tolist(),
                )
                return updated
    finally:
        _close_wizard(capture, window)
