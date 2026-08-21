"""ChArUco sample collection, coverage metrics, and pose novelty evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

# Frame acceptance policy shared by the live wizard and the folder workflow.
DEFAULT_SHARPNESS_THRESHOLD = 80.0
MIN_CHARUCO_CORNERS = 13
# Fraction of the frame that the board must keep away from each border.
BOARD_EDGE_MARGIN = 0.03
# Weighted distance components: centroid X/Y, area, horizontal/vertical tilt.
SIGNATURE_WEIGHTS = np.array([1.0, 1.0, 2.5, 2.0, 2.0])
# Minimum weighted distance between two captured board poses. Live capture
# sees near-duplicate frames continuously and tolerates a larger threshold
# than the folder workflow, which processes deliberately distinct photos.
LIVE_POSE_NOVELTY_THRESHOLD = 0.16
FOLDER_POSE_NOVELTY_THRESHOLD = 0.04
SIGNATURE_EPSILON = 1e-9
MIN_HOMOGRAPHY_CORNERS = 4

# Coverage requirements for a trustworthy intrinsic calibration.
COVERAGE_GRID_SIZE = 3
MIN_COVERAGE_SAMPLES = 24
MIN_FAR_SAMPLES = 3
MIN_MEDIUM_SAMPLES = 6
MIN_NEAR_SAMPLES = 3
MIN_TILT_SAMPLES_PER_DIRECTION = 2
FAR_AREA_MAX = 0.10
MEDIUM_AREA_MAX = 0.25
TILT_SIGNATURE_THRESHOLD = 0.13


@dataclass
class CalibrationSample:
    corners: NDArray[np.float32]
    ids: NDArray[np.int32]
    image_path: Path | None = None
    signature: NDArray[np.float64] | None = None


def frame_sharpness(gray: NDArray[np.uint8]) -> float:
    """Laplacian variance: higher values mean a sharper image."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def board_within_margins(
    points: NDArray[np.floating],
    width: int,
    height: int,
    margin: float = BOARD_EDGE_MARGIN,
) -> bool:
    return bool(
        points[:, 0].min() > width * margin
        and points[:, 0].max() < width * (1.0 - margin)
        and points[:, 1].min() > height * margin
        and points[:, 1].max() < height * (1.0 - margin)
    )


def signature_distance(
    signature: NDArray[np.float64], previous: NDArray[np.float64]
) -> float:
    return float(np.linalg.norm((signature - previous) * SIGNATURE_WEIGHTS))


def is_novel_signature(
    signature: NDArray[np.float64],
    previous_signatures: list[NDArray[np.float64]],
    threshold: float,
) -> bool:
    if not previous_signatures:
        return True
    return (
        min(signature_distance(signature, previous) for previous in previous_signatures)
        > threshold
    )


@dataclass
class Coverage:
    """Tracks capture diversity (position, distance, tilt) during calibration."""

    grid: NDArray[np.int32] = field(
        default_factory=lambda: np.zeros(
            (COVERAGE_GRID_SIZE, COVERAGE_GRID_SIZE), dtype=np.int32
        )
    )
    scales: dict[str, int] = field(default_factory=lambda: {"far": 0, "medium": 0, "near": 0})
    tilts: dict[str, int] = field(
        default_factory=lambda: {"left": 0, "right": 0, "up": 0, "down": 0}
    )

    def register(self, signature: NDArray[np.float64]) -> None:
        x, y, area, horizontal_tilt, vertical_tilt = signature
        last = COVERAGE_GRID_SIZE - 1
        row = min(last, int(y * COVERAGE_GRID_SIZE))
        column = min(last, int(x * COVERAGE_GRID_SIZE))
        self.grid[row, column] += 1
        if area < FAR_AREA_MAX:
            self.scales["far"] += 1
        elif area < MEDIUM_AREA_MAX:
            self.scales["medium"] += 1
        else:
            self.scales["near"] += 1
        if horizontal_tilt > TILT_SIGNATURE_THRESHOLD:
            self.tilts["right"] += 1
        elif horizontal_tilt < -TILT_SIGNATURE_THRESHOLD:
            self.tilts["left"] += 1
        if vertical_tilt > TILT_SIGNATURE_THRESHOLD:
            self.tilts["down"] += 1
        elif vertical_tilt < -TILT_SIGNATURE_THRESHOLD:
            self.tilts["up"] += 1

    def rebuild(self, samples: list[CalibrationSample]) -> None:
        self.grid.fill(0)
        self.scales = {"far": 0, "medium": 0, "near": 0}
        self.tilts = {"left": 0, "right": 0, "up": 0, "down": 0}
        for sample in samples:
            if sample.signature is not None:
                self.register(sample.signature)

    def complete(self, sample_count: int) -> bool:
        return (
            sample_count >= MIN_COVERAGE_SAMPLES
            and bool(np.all(self.grid > 0))
            and self.scales["far"] >= MIN_FAR_SAMPLES
            and self.scales["medium"] >= MIN_MEDIUM_SAMPLES
            and self.scales["near"] >= MIN_NEAR_SAMPLES
            and all(count >= MIN_TILT_SAMPLES_PER_DIRECTION for count in self.tilts.values())
        )

    def instruction(self) -> str:
        missing = np.argwhere(self.grid == 0)
        if len(missing):
            row, column = missing[0]
            vertical = ("alto", "centro", "basso")[row]
            horizontal = ("sinistra", "centro", "destra")[column]
            return f"Sposta la board: {vertical} {horizontal}"
        if self.scales["far"] < MIN_FAR_SAMPLES:
            return "Allontana la board"
        if self.scales["medium"] < MIN_MEDIUM_SAMPLES:
            return "Porta la board a distanza media"
        if self.scales["near"] < MIN_NEAR_SAMPLES:
            return "Avvicina la board"
        for direction, label in (
            ("left", "Inclina il lato sinistro verso la camera"),
            ("right", "Inclina il lato destro verso la camera"),
            ("up", "Inclina il lato superiore verso la camera"),
            ("down", "Inclina il lato inferiore verso la camera"),
        ):
            if self.tilts[direction] < MIN_TILT_SAMPLES_PER_DIRECTION:
                return label
        return "Copertura completa: attendi il calcolo"


def _quad_from_homography(
    board, corners: NDArray[np.float32], ids: NDArray[np.int32]
) -> NDArray[np.float64] | None:
    chessboard = np.asarray(board.getChessboardCorners(), dtype=np.float32)
    identifiers = ids.reshape(-1)
    if len(identifiers) < MIN_HOMOGRAPHY_CORNERS:
        return None
    source = chessboard[identifiers, :2]
    destination = corners.reshape(-1, 2)
    homography, _ = cv2.findHomography(source, destination, 0)
    if homography is None:
        return None
    size = board.getChessboardSize()
    square = float(board.getSquareLength())
    outer = np.array(
        [
            [
                [0, 0],
                [size[0] * square, 0],
                [size[0] * square, size[1] * square],
                [0, size[1] * square],
            ]
        ],
        dtype=np.float32,
    )
    return cv2.perspectiveTransform(outer, homography).reshape(4, 2).astype(np.float64)


def sample_signature(
    board, corners: NDArray[np.float32], ids: NDArray[np.int32], image_size: tuple[int, int]
) -> NDArray[np.float64] | None:
    """Normalized board pose signature: centroid, area and tilt components."""
    quad = _quad_from_homography(board, corners, ids)
    if quad is None:
        return None
    width, height = image_size
    centroid = np.mean(quad, axis=0) / np.array([width, height])
    area = abs(float(cv2.contourArea(quad.astype(np.float32)))) / (width * height)
    top = np.linalg.norm(quad[1] - quad[0])
    bottom = np.linalg.norm(quad[2] - quad[3])
    left = np.linalg.norm(quad[3] - quad[0])
    right = np.linalg.norm(quad[2] - quad[1])
    horizontal_tilt = (left - right) / max(left + right, SIGNATURE_EPSILON)
    vertical_tilt = (top - bottom) / max(top + bottom, SIGNATURE_EPSILON)
    return np.array([centroid[0], centroid[1], area, horizontal_tilt, vertical_tilt])
