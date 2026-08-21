"""Multi-view global bundle adjustment for reference marker stitching in large arenas."""

from __future__ import annotations

import argparse
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray
from scipy.optimize import least_squares

from ..core.aruco import aruco_dictionary
from ..core.config import (
    AnchorFrameConfig,
    AppConfig,
    CameraConfig,
    ReferenceMarkerConfig,
    save_json,
)
from ..pipeline.capture import apply_camera_properties, apply_digital_zoom, open_video_capture
from ..transport.diagnostics import configure_diagnostics
from ..transport.diagnostics import event as diagnostic_event
from .planar import (
    ControlPointPicker,
    annotated_reference_image,
    config_with_anchor_frame,
    config_with_references,
    detect_marker_centers,
    selected_capture_cameras,
)

WINDOW_NAME = "VisionSystem - acquisizione stitching"
HEADER_HEIGHT = 150
TILE_WIDTH = 560
TILE_HEIGHT = 315
GRID_COLUMNS = 2
AUTO_CAPTURE_STABLE_FRAMES = 4
AUTO_CAPTURE_MIN_REFERENCES_PER_CAMERA = 2
HOMOGRAPHY_MAX_WIDTH = 1600
HOMOGRAPHY_MAX_HEIGHT = 1000
HOMOGRAPHY_MIN_WIDTH = 640
HOMOGRAPHY_MIN_HEIGHT = 480
LOGGER = logging.getLogger(__name__)


@dataclass
class StitchResult:
    references: list[ReferenceMarkerConfig]
    homographies: dict[str, NDArray[np.float64]]
    rms_errors: dict[str, float]
    detected_ids: dict[str, list[int]]
    dropped_markers: list[int]
    detached_images: list[str]
    used_anchors: list[int]

    @property
    def rms_total(self) -> float:
        if not self.rms_errors:
            return 0.0
        values = np.array(list(self.rms_errors.values()))
        return float(np.sqrt(np.mean(values * values)))


def _local_corners(size_m: float) -> NDArray[np.float64]:
    half = size_m / 2
    return np.array([[-half, half], [half, half], [half, -half], [-half, -half]], dtype=np.float64)


def _world_corners(position: NDArray[np.float64], yaw: float, size_m: float) -> NDArray[np.float64]:
    cosine, sine = math.cos(yaw), math.sin(yaw)
    rotation = np.array([[cosine, -sine], [sine, cosine]], dtype=np.float64)
    return position + (_local_corners(size_m) @ rotation.T)


def _apply_homographies(
    homographies: NDArray[np.float64], points: NDArray[np.float64]
) -> NDArray[np.float64]:
    homogeneous = np.concatenate([points, np.ones((len(points), 1))], axis=1)
    projected = homographies @ homogeneous[..., None]
    return projected[..., :2, 0] / projected[..., 2, 0][..., None]


def _side_lengths(corners: NDArray[np.float64]) -> NDArray[np.float64]:
    points = np.asarray(corners, dtype=np.float64).reshape(4, 2)
    return np.array([np.linalg.norm(points[(index + 1) % 4] - points[index]) for index in range(4)])


def _estimate_yaw(world_corners: NDArray[np.float64], local: NDArray[np.float64]) -> float:
    center_w = np.mean(world_corners, axis=0)
    centered = world_corners - center_w
    cross = float(np.sum(local[:, 0] * centered[:, 1] - local[:, 1] * centered[:, 0]))
    dot = float(np.sum(local[:, 0] * centered[:, 0] + local[:, 1] * centered[:, 1]))
    return math.atan2(cross, dot)


def _homography_from_correspondences(
    pixels: NDArray[np.float64], world: NDArray[np.float64]
) -> NDArray[np.float64] | None:
    if len(pixels) < 3:
        return None
    if len(pixels) == 3:
        affine = cv2.getAffineTransform(pixels.astype(np.float32), world.astype(np.float32))
        return np.vstack((affine, [0.0, 0.0, 1.0]))
    homography, _ = cv2.findHomography(pixels, world, method=0)
    return None if homography is None else np.asarray(homography, dtype=np.float64)


def _similarity_from_correspondences(
    source: NDArray[np.float64], target: NDArray[np.float64]
) -> NDArray[np.float64] | None:
    """Return the exact similarity determined by two point correspondences."""
    if len(source) != 2 or len(target) != 2:
        return None
    source_delta = source[1] - source[0]
    target_delta = target[1] - target[0]
    source_length = float(np.linalg.norm(source_delta))
    target_length = float(np.linalg.norm(target_delta))
    if min(source_length, target_length) < 1e-9:
        return None
    angle = math.atan2(float(target_delta[1]), float(target_delta[0])) - math.atan2(
        float(source_delta[1]), float(source_delta[0])
    )
    scale = target_length / source_length
    linear = scale * np.array(
        [[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]]
    )
    translation = target[0] - linear @ source[0]
    return np.vstack((np.column_stack((linear, translation)), [0.0, 0.0, 1.0]))


def _unflatten_homographies(flat: NDArray[np.float64]) -> NDArray[np.float64]:
    count = len(flat) // 8
    matrix = np.empty((count, 3, 3), dtype=np.float64)
    values = flat[: count * 8].reshape(count, 8)
    matrix[:, 0, :] = values[:, 0:3]
    matrix[:, 1, :] = values[:, 3:6]
    matrix[:, 2, :2] = values[:, 6:8]
    matrix[:, 2, 2] = 1.0
    return matrix


def _flatten_homographies(matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    normalized = matrix / matrix[:, 2, 2][:, None, None]
    return normalized.reshape(len(matrix), 9)[:, :8].reshape(-1)


def _anchor_init(
    corners_by_id: dict[int, NDArray[np.float64]],
    anchors: dict[int, NDArray[np.float64]],
) -> NDArray[np.float64] | None:
    visible = [marker_id for marker_id in anchors if marker_id in corners_by_id]
    if len(visible) < 3:
        return None
    pixels = np.asarray(
        [np.mean(corners_by_id[marker_id], axis=0) for marker_id in visible],
        dtype=np.float64,
    )
    world = np.asarray([anchors[marker_id] for marker_id in visible], dtype=np.float64)
    return _homography_from_correspondences(pixels, world)


def _similarity_bootstrap(
    corners_by_id: dict[int, NDArray[np.float64]], marker_size_m: float
) -> NDArray[np.float64]:
    """Build a well-conditioned similarity init from the largest visible marker.

    A homography estimated from a single small marker extrapolates catastrophically
    to distant pixels; an affine built from the same three corners only degrades
    linearly with distance and gives the optimizer a sane starting point.
    """
    largest_id = max(
        corners_by_id,
        key=lambda marker_id: float(np.mean(_side_lengths(corners_by_id[marker_id]))),
    )
    corners = np.asarray(corners_by_id[largest_id], dtype=np.float64)
    affine = cv2.getAffineTransform(
        corners[[0, 1, 3]].astype(np.float32),
        _local_corners(marker_size_m)[[0, 1, 3]].astype(np.float32),
    )
    return np.vstack((affine, [0.0, 0.0, 1.0]))


def _metric_bootstrap(
    corners_by_id: dict[int, NDArray[np.float64]], marker_size_m: float
) -> NDArray[np.float64]:
    """Estimate an affine metric frame from all visible square markers.

    A bootstrap based on one marker inherits that marker's corner noise.  The
    orthogonal, equal-length edge constraints from every visible square provide
    a much stabler arena-scale estimate.  The later projective bundle adjustment
    is still free to model camera perspective.
    """
    constraints: list[NDArray[np.float64]] = []
    edges: list[NDArray[np.float64]] = []
    for raw_corners in corners_by_id.values():
        corners = np.asarray(raw_corners, dtype=np.float64).reshape(4, 2)
        edge_x = ((corners[1] - corners[0]) + (corners[2] - corners[3])) / 2
        edge_y = ((corners[0] - corners[3]) + (corners[1] - corners[2])) / 2
        if min(np.linalg.norm(edge_x), np.linalg.norm(edge_y)) < 1e-6:
            continue
        orthogonal = np.array(
            [
                edge_x[0] * edge_y[0],
                edge_x[0] * edge_y[1] + edge_x[1] * edge_y[0],
                edge_x[1] * edge_y[1],
            ]
        )
        equal_length = np.array(
            [
                edge_x[0] ** 2 - edge_y[0] ** 2,
                2 * (edge_x[0] * edge_x[1] - edge_y[0] * edge_y[1]),
                edge_x[1] ** 2 - edge_y[1] ** 2,
            ]
        )
        constraints.extend(
            equation / np.linalg.norm(equation)
            for equation in (orthogonal, equal_length)
            if np.linalg.norm(equation) > 1e-12
        )
        edges.extend((edge_x, edge_y))
    if len(constraints) < 2:
        return _similarity_bootstrap(corners_by_id, marker_size_m)

    _, _, vh = np.linalg.svd(np.asarray(constraints))
    metric_values = vh[-1]
    metric = np.array([[metric_values[0], metric_values[1]], [metric_values[1], metric_values[2]]])
    if np.trace(metric) < 0:
        metric = -metric
    eigenvalues, eigenvectors = np.linalg.eigh(metric)
    if float(np.min(eigenvalues)) <= 1e-12:
        return _similarity_bootstrap(corners_by_id, marker_size_m)
    affine = eigenvectors @ np.diag(np.sqrt(eigenvalues)) @ eigenvectors.T
    mapped_lengths = [float(np.linalg.norm(affine @ edge)) for edge in edges]
    median_length = float(np.median(mapped_lengths))
    if median_length <= 1e-12:
        return _similarity_bootstrap(corners_by_id, marker_size_m)
    affine *= marker_size_m / median_length

    reference_id = max(
        corners_by_id,
        key=lambda marker_id: float(np.mean(_side_lengths(corners_by_id[marker_id]))),
    )
    reference = np.asarray(corners_by_id[reference_id], dtype=np.float64)
    direction = affine @ (reference[1] - reference[0])
    angle = math.atan2(float(direction[1]), float(direction[0]))
    rotate = np.array([[math.cos(angle), math.sin(angle)], [-math.sin(angle), math.cos(angle)]])
    affine = rotate @ affine
    center = np.mean(reference, axis=0)
    translation = -(affine @ center)
    return np.vstack((np.column_stack((affine, translation)), [0.0, 0.0, 1.0]))


def _components(
    aligned_names: list[str], kept_ids: list[int], observations: dict[str, dict[int, NDArray]]
) -> list[list[int]]:
    parent: dict[int, int] = {}

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    image_nodes = list(range(len(aligned_names)))
    marker_nodes = {
        marker_id: len(aligned_names) + index for index, marker_id in enumerate(kept_ids)
    }
    for node in list(image_nodes) + list(marker_nodes.values()):
        parent[node] = node
    for image_index, name in enumerate(aligned_names):
        for marker_id in observations[name]:
            if marker_id in marker_nodes:
                union(image_index, marker_nodes[marker_id])
    groups: dict[int, list[int]] = {}
    for marker_id, node in marker_nodes.items():
        groups.setdefault(find(node), []).append(marker_id)
    return list(groups.values())


def stitch_reference_markers(
    observations: dict[str, dict[int, NDArray[np.float64]]],
    anchors: dict[int, NDArray[np.float64]],
    marker_size_m: float,
    plane_z_m: float = 0.0,
    uncertainty_m: float = 0.005,
) -> StitchResult:
    """Stitch per-image ArUco observations into one planar reference map.

    ``observations`` maps an image name to ``{marker_id: pixel corners (4, 2)}``.
    ``anchors`` maps marker ids to their known world XY positions. Every image
    is aligned either through the anchors it sees (3 or 4) or through markers
    shared with already aligned images; a global least squares refinement then
    solves marker positions/yaws and image homographies together.
    """
    if marker_size_m <= 0:
        raise ValueError("marker_size_m deve essere positivo")
    image_names = sorted(observations)
    if not image_names:
        raise ValueError("nessuna immagine fornita")
    all_marker_ids = sorted({marker_id for image in observations.values() for marker_id in image})
    if not all_marker_ids:
        raise ValueError("nessun marker rilevato")

    homographies: dict[str, NDArray[np.float64]] = {}
    for name in image_names:
        found = _anchor_init(observations[name], anchors)
        if found is not None:
            homographies[name] = found
            diagnostic_event(
                LOGGER,
                "stitch_image_anchored",
                image=name,
                anchors=sorted(set(observations[name]) & set(anchors)),
            )

    local = _local_corners(marker_size_m)
    if not homographies:
        # A camera may legitimately see no markers at an arena edge. Start from
        # the richest image so empty views are reported as detached instead of
        # failing inside ``max()`` in the similarity bootstrap.
        bootstrap = max(image_names, key=lambda name: len(observations[name]))
        homographies[bootstrap] = _metric_bootstrap(observations[bootstrap], marker_size_m)
        diagnostic_event(LOGGER, "stitch_image_bootstrapped", image=bootstrap)

    def compute_states() -> None:
        states.clear()
        candidates: dict[int, list[tuple[float, float, float]]] = {}
        for name in homographies:
            for marker_id, corners in observations[name].items():
                mapped = _apply_homographies(
                    homographies[name], np.asarray(corners, dtype=np.float64)
                )
                center = np.mean(mapped, axis=0)
                yaw = _estimate_yaw(mapped, local)
                candidates.setdefault(marker_id, []).append(
                    (float(center[0]), float(center[1]), yaw)
                )
        for marker_id, values in candidates.items():
            array = np.asarray(values)
            yaw = math.atan2(
                float(np.mean(np.sin(array[:, 2]))),
                float(np.mean(np.cos(array[:, 2]))),
            )
            states[marker_id] = (
                float(np.mean(array[:, 0])),
                float(np.mean(array[:, 1])),
                yaw,
            )

    states: dict[int, tuple[float, float, float]] = {}
    compute_states()
    for _ in range(len(image_names)):
        progressed = False
        for name in image_names:
            if name in homographies:
                continue
            pixels: list[NDArray[np.float64]] = []
            world: list[NDArray[np.float64]] = []
            for marker_id, corners in observations[name].items():
                if marker_id in states:
                    x, y, yaw = states[marker_id]
                    world.append(_world_corners(np.array([x, y]), yaw, marker_size_m))
                    pixels.append(np.asarray(corners, dtype=np.float64))
            if not world:
                continue
            homography = _homography_from_correspondences(
                np.concatenate(pixels), np.concatenate(world)
            )
            if homography is None:
                continue
            homographies[name] = homography
            progressed = True
            diagnostic_event(
                LOGGER,
                "stitch_image_propagated",
                image=name,
                shared_markers=[
                    marker_id for marker_id in observations[name] if marker_id in states
                ],
                weak_single_marker_overlap=len(world) == 1,
            )
        if not progressed:
            break
        compute_states()

    # In a large arena the anchors are normally spread near the perimeter, so
    # no individual camera may see three of them. Once overlapping views have
    # formed a connected relative map, align that whole map using the anchors
    # observed collectively across all cameras.
    compute_states()
    visible_anchors = sorted(set(states) & set(anchors))
    anchor_alignment: NDArray[np.float64] | None = None
    if len(visible_anchors) >= 3:
        anchor_alignment = _homography_from_correspondences(
            np.asarray([states[marker_id][:2] for marker_id in visible_anchors]),
            np.asarray([anchors[marker_id] for marker_id in visible_anchors]),
        )
    elif len(visible_anchors) == 2:
        anchor_alignment = _similarity_from_correspondences(
            np.asarray([states[marker_id][:2] for marker_id in visible_anchors]),
            np.asarray([anchors[marker_id] for marker_id in visible_anchors]),
        )
    if anchor_alignment is not None:
        for name in homographies:
            aligned = anchor_alignment @ homographies[name]
            homographies[name] = aligned / aligned[2, 2]
        compute_states()
        diagnostic_event(
            LOGGER,
            "stitch_map_aligned_from_collective_anchors",
            anchors=visible_anchors,
        )

    detached_images = [name for name in image_names if name not in homographies]
    if detached_images:
        diagnostic_event(
            LOGGER,
            "stitch_images_detached",
            level=logging.WARNING,
            images=detached_images,
        )
    aligned_names = [name for name in image_names if name in homographies]

    dropped_markers = [
        marker_id
        for marker_id in all_marker_ids
        if not any(marker_id in observations[name] for name in aligned_names)
    ]
    kept_ids = [marker_id for marker_id in all_marker_ids if marker_id not in dropped_markers]
    compute_states()
    # Anchors are measured truths, not soft hints. Force their centers to the
    # requested rectangle before optimization; all other marker poses and all
    # camera homographies adapt around these exact world coordinates.
    for marker_id in visible_anchors:
        _, _, yaw = states[marker_id]
        anchor_position = anchors[marker_id]
        states[marker_id] = (
            float(anchor_position[0]),
            float(anchor_position[1]),
            yaw,
        )

    fixed_pos = {marker_id: marker_id in anchors for marker_id in kept_ids}
    fixed_yaw: dict[int, bool] = {}
    for component in _components(aligned_names, kept_ids, observations):
        anchor_markers = [marker_id for marker_id in component if marker_id in anchors]
        if len(anchor_markers) >= 2:
            continue
        if len(anchor_markers) == 1:
            fixed_yaw[anchor_markers[0]] = True
        else:
            first = component[0]
            fixed_pos[first] = True
            fixed_yaw[first] = True

    variable_entries: list[tuple[int, str]] = []
    variable_index: dict[tuple[int, str], int] = {}
    for marker_id in kept_ids:
        for kind in ("x", "y", "yaw"):
            if kind in ("x", "y") and fixed_pos[marker_id]:
                continue
            if kind == "yaw" and fixed_yaw.get(marker_id, False):
                continue
            variable_index[(marker_id, kind)] = len(variable_entries)
            variable_entries.append((marker_id, kind))

    num_images = len(aligned_names)
    marker_index = {marker_id: index for index, marker_id in enumerate(kept_ids)}
    image_index = {name: index for index, name in enumerate(aligned_names)}
    observation_records: list[tuple[int, int, NDArray[np.float64]]] = []
    for name in aligned_names:
        for marker_id, corners in observations[name].items():
            if marker_id in marker_index:
                observation_records.append(
                    (image_index[name], marker_index[marker_id], np.asarray(corners))
                )
    observation_records.sort(key=lambda item: (item[0], item[1]))
    pixel_corners = np.asarray([item[2] for item in observation_records])
    record_images = np.asarray([item[0] for item in observation_records], dtype=np.int64)
    record_markers = np.asarray([item[1] for item in observation_records], dtype=np.int64)

    marker_offset = num_images * 8
    num_variables = marker_offset + len(variable_entries)

    def homographies_from(vector: NDArray[np.float64]) -> NDArray[np.float64]:
        return _unflatten_homographies(vector[:marker_offset])

    def marker_matrix_from(vector: NDArray[np.float64]) -> NDArray[np.float64]:
        matrix = np.zeros((len(kept_ids), 3))
        for marker_id in kept_ids:
            x, y, yaw = states[marker_id]
            if not fixed_pos[marker_id]:
                x = vector[marker_offset + variable_index[(marker_id, "x")]]
                y = vector[marker_offset + variable_index[(marker_id, "y")]]
            if not fixed_yaw.get(marker_id, False):
                yaw = vector[marker_offset + variable_index[(marker_id, "yaw")]]
            matrix[marker_index[marker_id]] = (x, y, yaw)
        return matrix

    def initial_vector() -> NDArray[np.float64]:
        vector = np.zeros(num_variables)
        vector[:marker_offset] = _flatten_homographies(
            np.asarray([homographies[name] for name in aligned_names])
        )
        for (marker_id, kind), index in variable_index.items():
            x, y, yaw = states[marker_id]
            vector[marker_offset + index] = {"x": x, "y": y, "yaw": yaw}[kind]
        return vector

    def residual(vector: NDArray[np.float64]) -> NDArray[np.float64]:
        homographies_matrix = homographies_from(vector)
        marker_matrix = marker_matrix_from(vector)
        x_world, y_world, yaw_world = (
            marker_matrix[:, 0],
            marker_matrix[:, 1],
            marker_matrix[:, 2],
        )
        cosine, sine = np.cos(yaw_world), np.sin(yaw_world)
        # Rotate row-vector marker corners with R.T. Keep the equations
        # explicit: the previous stack/transposition built R.T and then
        # transposed it once more, effectively optimizing the opposite yaw.
        world_corners = np.empty((len(kept_ids), 4, 2), dtype=np.float64)
        world_corners[:, :, 0] = (
            x_world[:, None]
            + cosine[:, None] * local[None, :, 0]
            - sine[:, None] * local[None, :, 1]
        )
        world_corners[:, :, 1] = (
            y_world[:, None]
            + sine[:, None] * local[None, :, 0]
            + cosine[:, None] * local[None, :, 1]
        )
        projected_world = _apply_homographies(
            homographies_matrix[np.repeat(record_images, 4)],
            pixel_corners.reshape(-1, 2),
        )
        return (projected_world.reshape(-1, 4, 2) - world_corners[record_markers]).reshape(-1)

    solution = least_squares(
        residual,
        initial_vector(),
        method="trf",
        loss="linear",
        x_scale="jac",
        ftol=1e-12,
        xtol=1e-12,
        gtol=1e-12,
        max_nfev=300,
    )
    refined = least_squares(
        residual,
        solution.x,
        method="trf",
        loss="soft_l1",
        f_scale=marker_size_m / 7,
        x_scale="jac",
        ftol=1e-12,
        xtol=1e-12,
        gtol=1e-12,
        max_nfev=300,
    )
    final = refined.x
    final_rms = float(np.sqrt(np.mean(refined.fun**2)))

    solved_homographies = {
        name: homographies_from(final)[image_index[name]] for name in aligned_names
    }
    final_states: dict[int, tuple[float, float, float]] = {}
    for marker_id in kept_ids:
        x, y, yaw = states[marker_id]
        if not fixed_pos[marker_id]:
            x = final[marker_offset + variable_index[(marker_id, "x")]]
            y = final[marker_offset + variable_index[(marker_id, "y")]]
        if not fixed_yaw.get(marker_id, False):
            yaw = final[marker_offset + variable_index[(marker_id, "yaw")]]
        final_states[marker_id] = (float(x), float(y), float(yaw))

    marker_squared_errors: dict[int, list[float]] = {marker_id: [] for marker_id in kept_ids}
    for name in aligned_names:
        for marker_id, corners in observations[name].items():
            if marker_id not in marker_index:
                continue
            x, y, yaw = final_states[marker_id]
            mapped = _apply_homographies(solved_homographies[name], np.asarray(corners))
            expected = _world_corners(np.array([x, y]), yaw, marker_size_m)
            marker_squared_errors[marker_id].extend(
                np.sum((mapped - expected) ** 2, axis=1).tolist()
            )
    marker_rms_errors = {
        marker_id: math.sqrt(sum(values) / len(values)) if values else 0.0
        for marker_id, values in marker_squared_errors.items()
    }

    references: list[ReferenceMarkerConfig] = []
    for marker_id in kept_ids:
        x, y, yaw = final_states[marker_id]
        seen = sum(1 for name in aligned_names if marker_id in observations[name])
        half_yaw = yaw / 2
        references.append(
            ReferenceMarkerConfig(
                id=marker_id,
                size_m=marker_size_m,
                position_m=(x, y, plane_z_m),
                orientation_xyzw=(0.0, 0.0, math.sin(half_yaw), math.cos(half_yaw)),
                uncertainty_m=max(
                    uncertainty_m / math.sqrt(max(seen, 1)),
                    marker_rms_errors[marker_id],
                ),
                name=f"ref-{marker_id}",
            )
        )
    references.sort(key=lambda marker: marker.id)

    rms_errors: dict[str, float] = {}
    for name in aligned_names:
        squared: list[float] = []
        for marker_id, corners in observations[name].items():
            if marker_id not in marker_index:
                continue
            x, y, yaw = final_states[marker_id]
            mapped = _apply_homographies(solved_homographies[name], np.asarray(corners))
            expected = _world_corners(np.array([x, y]), yaw, marker_size_m)
            squared.extend(np.sum((mapped - expected) ** 2, axis=1).tolist())
        rms_errors[name] = math.sqrt(sum(squared) / len(squared)) if squared else 0.0

    diagnostic_event(
        LOGGER,
        "stitch_solved",
        images=aligned_names,
        detached_images=detached_images,
        markers=kept_ids,
        dropped_markers=dropped_markers,
        anchors=visible_anchors,
        rms_errors=rms_errors,
        marker_rms_errors=marker_rms_errors,
        rms_total=math.sqrt(sum(value * value for value in rms_errors.values()) / len(rms_errors))
        if rms_errors
        else 0.0,
        reprojection_residuals_final=final_rms,
    )
    return StitchResult(
        references=references,
        homographies=solved_homographies,
        rms_errors=rms_errors,
        detected_ids={name: sorted(observations[name]) for name in image_names},
        dropped_markers=dropped_markers,
        detached_images=detached_images,
        used_anchors=visible_anchors,
    )


@dataclass
class CapturePanel:
    camera: CameraConfig
    capture: cv2.VideoCapture
    frame: NDArray[np.uint8] | None = None
    detected_ids: list[int] = field(default_factory=list)


def _camera_overlap_status(
    detected_by_camera: dict[str, set[int]],
    minimum_references: int = AUTO_CAPTURE_MIN_REFERENCES_PER_CAMERA,
) -> tuple[list[tuple[str, str, list[int]]], bool]:
    """Return shared-reference links and whether the live scene is ready."""
    camera_ids = sorted(detected_by_camera)
    links: list[tuple[str, str, list[int]]] = []
    neighbours = {camera_id: set() for camera_id in camera_ids}
    for left_index, left in enumerate(camera_ids):
        for right in camera_ids[left_index + 1 :]:
            shared = sorted(detected_by_camera[left] & detected_by_camera[right])
            if not shared:
                continue
            links.append((left, right, shared))
            neighbours[left].add(right)
            neighbours[right].add(left)
    if len(camera_ids) < 2:
        return links, False
    reached = {camera_ids[0]}
    pending = [camera_ids[0]]
    while pending:
        current = pending.pop()
        for neighbour in neighbours[current] - reached:
            reached.add(neighbour)
            pending.append(neighbour)
    every_camera_has_enough = all(
        len(detected_by_camera[camera_id]) >= minimum_references for camera_id in camera_ids
    )
    return links, len(reached) == len(camera_ids) and every_camera_has_enough


def render_stitched_homography(
    images: dict[str, NDArray[np.uint8]],
    result: StitchResult,
    marker_size_m: float,
    frame_ids: list[int] | None = None,
) -> tuple[NDArray[np.uint8], NDArray[np.float64], dict[int, tuple[float, float]]]:
    """Warp and blend all aligned camera views into a selectable top-down image."""
    if not result.references:
        raise ValueError("nessun reference da mostrare nell'omografia")
    positions = np.asarray([marker.position_m[:2] for marker in result.references])
    minimum = np.min(positions, axis=0)
    maximum = np.max(positions, axis=0)
    span = np.maximum(maximum - minimum, marker_size_m * 4)
    padding = max(float(np.max(span)) * 0.15, marker_size_m * 3, 0.15)
    minimum -= padding
    maximum += padding
    span = maximum - minimum
    scale = min(
        (HOMOGRAPHY_MAX_WIDTH - 80) / span[0],
        (HOMOGRAPHY_MAX_HEIGHT - 80) / span[1],
        400.0,
    )
    raw_width = int(math.ceil(span[0] * scale))
    raw_height = int(math.ceil(span[1] * scale))
    width = min(HOMOGRAPHY_MAX_WIDTH, max(HOMOGRAPHY_MIN_WIDTH, raw_width + 80))
    height = min(HOMOGRAPHY_MAX_HEIGHT, max(HOMOGRAPHY_MIN_HEIGHT, raw_height + 80))
    extra_x = (width - raw_width) / 2
    extra_y = (height - raw_height) / 2
    canvas_from_world = np.array(
        [
            [scale, 0.0, extra_x - scale * minimum[0]],
            [0.0, -scale, extra_y + scale * maximum[1]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    accumulated = np.zeros((height, width, 3), dtype=np.float64)
    weights = np.zeros((height, width), dtype=np.float64)
    camera_labels: list[tuple[str, tuple[int, int]]] = []
    for name, homography in sorted(result.homographies.items()):
        image = images.get(name)
        if image is None:
            continue
        canvas_from_image = canvas_from_world @ homography
        warped = cv2.warpPerspective(image, canvas_from_image, (width, height))
        source_mask = np.full(image.shape[:2], 255, dtype=np.uint8)
        mask = cv2.warpPerspective(
            source_mask,
            canvas_from_image,
            (width, height),
            flags=cv2.INTER_NEAREST,
        )
        valid = mask > 0
        accumulated[valid] += warped[valid]
        weights[valid] += 1
        ys, xs = np.nonzero(valid)
        if len(xs):
            camera_labels.append((name, (int(np.mean(xs)), int(np.mean(ys)))))

    mosaic = np.full((height, width, 3), 28, dtype=np.uint8)
    valid = weights > 0
    mosaic[valid] = np.clip(accumulated[valid] / weights[valid, None], 0, 255).astype(np.uint8)
    for name, point in camera_labels:
        cv2.putText(
            mosaic,
            name,
            point,
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 180, 40),
            2,
        )

    centers: dict[int, tuple[float, float]] = {}
    for marker in result.references:
        projected = _apply_homographies(
            canvas_from_world, np.asarray([marker.position_m[:2]], dtype=np.float64)
        )[0]
        point = tuple(np.rint(projected).astype(int))
        centers[marker.id] = (float(projected[0]), float(projected[1]))
        cv2.circle(mosaic, point, 8, (0, 220, 255), -1)
        cv2.putText(
            mosaic,
            f"ID {marker.id}",
            (point[0] + 10, point[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 220, 255),
            2,
        )
    if frame_ids is not None and all(marker_id in centers for marker_id in frame_ids):
        frame_points = np.asarray(
            [centers[marker_id] for marker_id in frame_ids],
            dtype=np.int32,
        ).reshape(-1, 1, 2)
        cv2.polylines(mosaic, [frame_points], True, (40, 255, 40), 3, cv2.LINE_AA)
    return mosaic, canvas_from_world, centers


def select_anchor_frame_from_homography(
    image: NDArray[np.uint8],
    centers: dict[int, tuple[float, float]],
    width_m: float | None,
    height_m: float | None,
) -> tuple[list[int], float, float] | None:
    if len(centers) < 4:
        raise ValueError(
            "servono almeno quattro reference collegati per selezionare origine, +X, +X+Y e +Y"
        )
    picker = ControlPointPicker(
        image=image,
        labels=["origine", "+X", "+X+Y", "+Y"],
        snap_centers=[centers[marker_id] for marker_id in sorted(centers)],
        snap_ids=sorted(centers),
        snap_radius_px=65.0,
    )
    if picker.run() is None:
        return None
    selected_ids = list(picker.selected_ids)
    selected_width = _positive_value(
        width_m,
        "Distanza reale X (origine -> +X) in metri: ",
    )
    selected_height = _positive_value(
        height_m,
        "Distanza reale Y (origine -> +Y) in metri: ",
    )
    return selected_ids, selected_width, selected_height


def capture_scene(
    cameras: list[CameraConfig],
    dictionary_name: str,
    shots_dir: Path,
    reference_ids: set[int],
) -> dict[str, Path] | None:
    detector = cv2.aruco.ArucoDetector(
        aruco_dictionary(dictionary_name), cv2.aruco.DetectorParameters()
    )
    panels: list[CapturePanel] = []
    for camera in cameras:
        capture = open_video_capture(camera.source)
        apply_camera_properties(capture, camera)
        panels.append(CapturePanel(camera=camera, capture=capture))
    shots_dir.mkdir(parents=True, exist_ok=True)
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    stable_frames = 0

    def save_current_frames(trigger: str) -> dict[str, Path] | None:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        saved: dict[str, Path] = {}
        for panel in panels:
            if panel.frame is None:
                continue
            path = shots_dir / f"{panel.camera.id}-{stamp}.jpg"
            cv2.imwrite(str(path), panel.frame)
            saved[panel.camera.id] = path
        if not saved:
            return None
        diagnostic_event(
            LOGGER,
            "stitch_capture_saved",
            shots={camera_id: str(path) for camera_id, path in saved.items()},
            trigger=trigger,
        )
        return saved

    try:
        while True:
            for panel in panels:
                ok, frame = panel.capture.read()
                panel.frame = apply_digital_zoom(frame, panel.camera.digital_zoom) if ok else None
                if panel.frame is not None:
                    _, ids, _ = detector.detectMarkers(panel.frame)
                    panel.detected_ids = (
                        sorted(
                            int(value) for value in ids.reshape(-1) if int(value) in reference_ids
                        )
                        if ids is not None
                        else []
                    )
                else:
                    panel.detected_ids = []
            detected_by_camera = {panel.camera.id: set(panel.detected_ids) for panel in panels}
            overlap_links, cameras_connected = _camera_overlap_status(detected_by_camera)
            stable_frames = stable_frames + 1 if cameras_connected else 0
            minimum_visible = min(
                (len(marker_ids) for marker_ids in detected_by_camera.values()),
                default=0,
            )
            rows = max(1, math.ceil(len(panels) / GRID_COLUMNS))
            canvas = np.full(
                (HEADER_HEIGHT + rows * TILE_HEIGHT, GRID_COLUMNS * TILE_WIDTH, 3),
                25,
                dtype=np.uint8,
            )
            lines = [
                "Acquisizione stitching: una foto per camera",
                "AUTO: stop con almeno 2 reference per camera e viste collegate",
                (
                    f"Minimo visibile: {minimum_visible}/2 | collegamenti: "
                    f"{len(overlap_links)} | stabilita: {stable_frames}/"
                    f"{AUTO_CAPTURE_STABLE_FRAMES}"
                ),
                "SPAZIO: scatta comunque | ESC: annulla",
            ]
            for index, line in enumerate(lines):
                cv2.putText(
                    canvas,
                    line,
                    (18, 30 + index * 34),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.68 if index else 0.76,
                    (80, 220, 255) if index else (240, 240, 240),
                    2,
                )
            for index, panel in enumerate(panels):
                row, column = divmod(index, GRID_COLUMNS)
                x = column * TILE_WIDTH
                y = HEADER_HEIGHT + row * TILE_HEIGHT
                if panel.frame is not None:
                    canvas[y : y + TILE_HEIGHT, x : x + TILE_WIDTH] = cv2.resize(
                        panel.frame, (TILE_WIDTH, TILE_HEIGHT)
                    )
                label = (
                    f"{index + 1}: {panel.camera.id} | reference: "
                    f"{','.join(map(str, panel.detected_ids)) or 'nessuno'}"
                )
                cv2.rectangle(canvas, (x + 7, y + 7), (x + TILE_WIDTH - 7, y + 45), (0, 0, 0), -1)
                cv2.putText(
                    canvas,
                    label,
                    (x + 15, y + 34),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    2,
                )
            cv2.imshow(WINDOW_NAME, canvas)
            key = cv2.waitKey(30) & 0xFF
            if key in (27, ord("q")):
                return None
            if stable_frames >= AUTO_CAPTURE_STABLE_FRAMES:
                saved = save_current_frames("auto_shared_references")
                if saved is not None:
                    return saved
            if key == ord(" "):
                saved = save_current_frames("manual")
                if saved is not None:
                    return saved
    finally:
        for panel in panels:
            panel.capture.release()
        cv2.destroyWindow(WINDOW_NAME)


def _load_stitch_config(path: Path) -> tuple[AppConfig, AnchorFrameConfig | None]:
    """Load a config, temporarily detaching an orphaned anchor frame.

    Other commands must reject an anchor frame whose marker records are
    missing.  The stitcher is the recovery path that recreates those records,
    so it may retain the frame metadata while validating the rest of the app
    configuration.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        return AppConfig.model_validate(payload), None
    except ValueError as original_error:
        if not isinstance(payload, dict) or not isinstance(payload.get("aruco"), dict):
            raise original_error
        aruco_payload = payload["aruco"]
        raw_anchor_frame = aruco_payload.get("anchor_frame")
        if raw_anchor_frame is None:
            raise original_error
        try:
            recovered_frame = AnchorFrameConfig.model_validate(raw_anchor_frame)
            repaired_payload = dict(payload)
            repaired_aruco = dict(aruco_payload)
            repaired_aruco["anchor_frame"] = None
            repaired_payload["aruco"] = repaired_aruco
            repaired_config = AppConfig.model_validate(repaired_payload)
        except ValueError:
            raise original_error from None
        return repaired_config, recovered_frame


def _anchor_ids_from_args(
    args: argparse.Namespace,
    config: AppConfig | None,
    recovered_frame: AnchorFrameConfig | None = None,
) -> tuple[list[int] | None, float | None, float | None]:
    configured_frame = (
        config.aruco.anchor_frame
        if config is not None and config.aruco.anchor_frame is not None
        else recovered_frame
    )
    if args.anchor_ids:
        if len(set(args.anchor_ids)) != 4:
            raise ValueError("--anchor-ids richiede quattro ID diversi")
        width_m = args.width_m
        height_m = args.height_m
        if configured_frame is not None:
            width_m = width_m if width_m is not None else configured_frame.x_distance_m
            height_m = height_m if height_m is not None else configured_frame.y_distance_m
        if width_m is None or height_m is None:
            raise ValueError("--anchor-ids richiede --width-m e --height-m (o un anchor_frame)")
        return list(args.anchor_ids), width_m, height_m
    if configured_frame is not None:
        frame = configured_frame
        if frame.opposite_id is None:
            raise ValueError("l'anchor_frame della config non ha opposite_id")
        return (
            [frame.origin_id, frame.x_axis_id, frame.opposite_id, frame.y_axis_id],
            frame.x_distance_m,
            frame.y_distance_m,
        )
    return None, args.width_m, args.height_m


def _positive_value(value: float | None, prompt: str) -> float:
    if value is None:
        value = float(input(prompt).strip())
    if value <= 0:
        raise ValueError(f"{prompt.split(':', 1)[0]} deve essere positivo")
    return value


def _detect(image: NDArray, dictionary_name: str) -> dict[int, NDArray[np.float64]]:
    _, detected_ids, corners, _ = detect_marker_centers(image, dictionary_name)
    by_id: dict[int, NDArray[np.float64]] = {}
    for marker_corners, marker_id_raw in zip(corners, detected_ids, strict=True):
        marker_id = int(marker_id_raw)
        by_id[marker_id] = np.asarray(marker_corners, dtype=np.float64).reshape(4, 2)
    return by_id


def reference_stitcher_main() -> None:
    parser = argparse.ArgumentParser(
        description="Mappa dei reference marker combinando foto di piu camere"
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--images", type=Path, nargs="+", help="foto da combinare")
    source_group.add_argument(
        "--camera",
        help="acquisizione live: 'all' scatta tutte le camere, altrimenti cam_X",
    )
    parser.add_argument("--shots-dir", type=Path, default=Path("photo/reference-stitch"))
    parser.add_argument("--config", type=Path, help="config con dictionary, anchor frame e misure")
    parser.add_argument(
        "--config-output",
        type=Path,
        help="scrive la config aggiornata qui; senza questa opzione sovrascrive --config",
    )
    parser.add_argument("--output", type=Path, default=Path("reference-markers.json"))
    parser.add_argument("--annotated-dir", type=Path)
    parser.add_argument(
        "--homography-output",
        type=Path,
        help="immagine top-down combinata; default accanto a --output",
    )
    parser.add_argument(
        "--select-frame",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "seleziona origine/+X/+X+Y/+Y sull'omografia; default attivo con --camera, "
            "disattivo con --images"
        ),
    )
    parser.add_argument(
        "--anchor-ids",
        type=int,
        nargs=4,
        metavar=("ORIGINE", "+X", "+X+Y", "+Y"),
        help="quattro ID anchor; default dall'anchor_frame della config",
    )
    parser.add_argument("--width-m", type=float, help="distanza origine -> +X in metri")
    parser.add_argument("--height-m", type=float, help="distanza origine -> +Y in metri")
    parser.add_argument("--marker-size-m", type=float, help="lato nero dei marker in metri")
    parser.add_argument("--plane-z-m", type=float, default=0.0)
    parser.add_argument("--uncertainty-m", type=float, default=0.005)
    parser.add_argument("--dictionary", default="DICT_4X4_50")
    parser.add_argument(
        "--ids",
        type=int,
        nargs="+",
        help="restringe gli ID; con --config devono essere reference configurati",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    configure_diagnostics("vision-reference-stitch")
    select_frame = (
        args.select_frame
        if args.select_frame is not None
        else args.camera is not None and args.anchor_ids is None
    )

    if args.config_output and not args.config:
        parser.error("--config-output richiede --config")
    if args.camera is not None and args.config is None:
        parser.error("--camera richiede --config per aprire le camere")
    if args.output.exists() and not args.force:
        parser.error(f"{args.output} esiste gia; usa --force per sovrascriverlo")
    homography_output = args.homography_output or args.output.with_name(
        f"{args.output.stem}-homography.jpg"
    )
    if homography_output.exists() and not args.force:
        parser.error(f"{homography_output} esiste gia; usa --force per sovrascriverlo")
    config: AppConfig | None = None
    recovered_frame: AnchorFrameConfig | None = None
    if args.config:
        try:
            config, recovered_frame = _load_stitch_config(args.config)
        except (OSError, ValueError) as error:
            parser.error(f"configurazione non valida in {args.config.resolve()}: {error}")
        if recovered_frame is not None:
            print(
                "ATTENZIONE: anchor_frame recuperato da una configurazione con reference "
                "incompleti; i record degli anchor verranno ricostruiti dagli scatti"
            )
    dictionary_name = config.aruco.dictionary if config is not None else args.dictionary
    try:
        anchor_ids, width_m, height_m = _anchor_ids_from_args(args, config, recovered_frame)
    except ValueError as error:
        parser.error(str(error))
    if select_frame:
        # Interactive selection defines a new physical frame. Do not silently
        # reuse dimensions from a previous anchor_frame: ask for the measured
        # truth unless the user supplied the values explicitly on the CLI.
        width_m = args.width_m
        height_m = args.height_m

    configured_reference_ids = (
        (
            set(config.aruco.reference_ids)
            or {marker.id for marker in config.aruco.reference_markers}
        )
        if config is not None
        else set()
    )
    if config is not None:
        if not configured_reference_ids:
            parser.error(
                "la configurazione non contiene aruco.reference_ids ne "
                "aruco.reference_markers: "
                "non ci sono ID ammessi per lo stitching"
            )
        requested_ids = set(args.ids) if args.ids else configured_reference_ids
        unknown_ids = requested_ids - configured_reference_ids
        if unknown_ids:
            parser.error(
                "--ids contiene marker che non sono reference nella configurazione: "
                f"{sorted(unknown_ids)}"
            )
        selected_ids: set[int] | None = requested_ids
    else:
        selected_ids = set(args.ids) if args.ids else None

    images_by_name: dict[str, NDArray] = {}
    if args.images is not None:
        for path in args.images:
            image = cv2.imread(str(path))
            if image is None:
                parser.error(f"impossibile leggere l'immagine: {path}")
            if path.name in images_by_name:
                parser.error(f"due immagini hanno lo stesso nome '{path.name}'; rinominale")
            images_by_name[path.name] = image
    else:
        try:
            cameras = selected_capture_cameras(config, args.camera)
        except ValueError as error:
            parser.error(str(error))
        assert selected_ids is not None
        shots = capture_scene(
            cameras,
            dictionary_name,
            args.shots_dir,
            selected_ids,
        )
        if shots is None:
            print("Operazione annullata: nessun file scritto")
            return
        for camera_id, path in shots.items():
            image = cv2.imread(str(path))
            if image is None:
                parser.error(f"impossibile leggere lo scatto: {path}")
            images_by_name[camera_id] = image

    mobile_ids = (
        {marker.id for marker in config.aruco.mobile_markers} if config is not None else set()
    )
    if selected_ids is not None and selected_ids & mobile_ids:
        parser.error(
            "--ids contiene marker mobili configurati, che non possono diventare reference: "
            f"{sorted(selected_ids & mobile_ids)}"
        )
    observations: dict[str, dict[int, NDArray[np.float64]]] = {}
    for name, image in images_by_name.items():
        by_id = _detect(image, dictionary_name)
        if selected_ids is not None:
            by_id = {
                marker_id: corners
                for marker_id, corners in by_id.items()
                if marker_id in selected_ids
            }
        elif mobile_ids:
            by_id = {
                marker_id: corners
                for marker_id, corners in by_id.items()
                if marker_id not in mobile_ids
            }
        observations[name] = by_id

    weak_overlap_pairs: list[tuple[str, str, int]] = []
    observation_names = sorted(observations)
    for left_index, left_name in enumerate(observation_names):
        for right_name in observation_names[left_index + 1 :]:
            shared = sorted(set(observations[left_name]) & set(observations[right_name]))
            if len(shared) == 1:
                weak_overlap_pairs.append((left_name, right_name, shared[0]))

    anchors: dict[int, NDArray[np.float64]] = {}
    if anchor_ids is not None and not select_frame:
        assert width_m is not None and height_m is not None
        anchors = {
            anchor_ids[0]: np.array([0.0, 0.0]),
            anchor_ids[1]: np.array([width_m, 0.0]),
            anchor_ids[2]: np.array([width_m, height_m]),
            anchor_ids[3]: np.array([0.0, height_m]),
        }
    if not anchors:
        LOGGER.warning(
            "nessun anchor: la mappa risultante avra un frame arbitrario "
            "(origine e orientazione relative alla prima foto)"
        )
    else:
        detected_anchor_ids = sorted(
            set(anchors).intersection(
                marker_id
                for image_observations in observations.values()
                for marker_id in image_observations
            )
        )
        required_anchor_count = 4 if config is not None else 2
        if len(detected_anchor_ids) < required_anchor_count:
            missing_anchor_ids = sorted(set(anchors) - set(detected_anchor_ids))
            parser.error(
                f"servono almeno {required_anchor_count} anchor visibili complessivamente; "
                f"rilevati: {detected_anchor_ids or 'nessuno'}; mancanti: {missing_anchor_ids}"
            )

    sizes: dict[int, float] = {}
    if config is not None:
        sizes.update({marker.id: marker.size_m for marker in config.aruco.reference_markers})
    unique_sizes = sorted(set(sizes.values()))
    if len(unique_sizes) == 1:
        marker_size_m = args.marker_size_m or unique_sizes[0]
    else:
        try:
            marker_size_m = _positive_value(args.marker_size_m, "Lato nero marker in metri: ")
        except (ValueError, EOFError) as error:
            parser.error(str(error))

    diagnostic_event(
        LOGGER,
        "stitch_started",
        images=sorted(images_by_name),
        anchors=sorted(anchors),
        marker_size_m=marker_size_m,
        plane_z_m=args.plane_z_m,
        dictionary=dictionary_name,
    )
    try:
        result = stitch_reference_markers(
            observations,
            anchors,
            marker_size_m,
            args.plane_z_m,
            args.uncertainty_m,
        )
    except ValueError as error:
        parser.error(str(error))
    if not result.references:
        parser.error("nessun marker ArUco rilevato nelle immagini")

    try:
        homography_image, _, homography_centers = render_stitched_homography(
            images_by_name, result, marker_size_m
        )
    except ValueError as error:
        parser.error(str(error))
    if select_frame:
        try:
            selected_frame = select_anchor_frame_from_homography(
                homography_image,
                homography_centers,
                width_m,
                height_m,
            )
        except (ValueError, EOFError) as error:
            parser.error(str(error))
        if selected_frame is None:
            print("Operazione annullata: nessun file di mappa scritto")
            return
        anchor_ids, width_m, height_m = selected_frame
        anchors = {
            anchor_ids[0]: np.array([0.0, 0.0]),
            anchor_ids[1]: np.array([width_m, 0.0]),
            anchor_ids[2]: np.array([width_m, height_m]),
            anchor_ids[3]: np.array([0.0, height_m]),
        }
        try:
            result = stitch_reference_markers(
                observations,
                anchors,
                marker_size_m,
                args.plane_z_m,
                args.uncertainty_m,
            )
            homography_image, _, _ = render_stitched_homography(
                images_by_name,
                result,
                marker_size_m,
                frame_ids=anchor_ids,
            )
        except ValueError as error:
            parser.error(str(error))
        print(
            "Frame selezionato: "
            f"origine={anchor_ids[0]} +X={anchor_ids[1]} "
            f"+X+Y={anchor_ids[2]} +Y={anchor_ids[3]} "
            f"larghezza={width_m:.3f}m altezza={height_m:.3f}m"
        )

    homography_output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(homography_output), homography_image):
        parser.error(f"impossibile scrivere l'omografia: {homography_output}")

    save_json(
        args.output,
        {"reference_markers": [marker.model_dump(mode="json") for marker in result.references]},
    )
    annotated_dir = args.annotated_dir or args.output.parent
    annotated_dir.mkdir(parents=True, exist_ok=True)
    for name, image in sorted(images_by_name.items()):
        _, detected_ids, corners, _ = detect_marker_centers(image, dictionary_name)
        filtered_detections = [
            (marker_id, marker_corners)
            for marker_id, marker_corners in zip(detected_ids, corners, strict=True)
            if selected_ids is None or marker_id in selected_ids
        ]
        detected_ids = [marker_id for marker_id, _ in filtered_detections]
        corners = [marker_corners for _, marker_corners in filtered_detections]
        annotated_path = annotated_dir / f"{name}-stitch-annotated.jpg"
        annotated = annotated_reference_image(
            image,
            corners,
            np.asarray(detected_ids, dtype=np.int32).reshape(-1, 1) if detected_ids else None,
            result.references,
            np.empty((0, 2), dtype=np.float64),
            [],
        )
        cv2.imwrite(str(annotated_path), annotated)
        print(f"Immagine annotata {name}: {annotated_path}")

    updated_config: AppConfig | None = None
    if config is not None:
        try:
            config_for_references = config
            if anchor_ids is not None:
                assert width_m is not None and height_m is not None
                aruco_without_frame = config.aruco.model_copy(update={"anchor_frame": None})
                config_for_references = config.model_copy(update={"aruco": aruco_without_frame})
            updated_config = config_with_references(config_for_references, result.references)
            if anchor_ids is not None:
                updated_config = config_with_anchor_frame(
                    updated_config, anchor_ids, width_m, height_m, args.plane_z_m
                )
        except ValueError as error:
            parser.error(f"configurazione non valida: {error}")
        target = args.config_output or args.config
        save_json(target, updated_config)

    print(f"Reference marker scritti in {args.output}")
    print(f"Omografia combinata scritta in {homography_output}")
    print(f"Marker mappati: {[marker.id for marker in result.references]}")
    if updated_config is not None:
        print(f"Configurazione aggiornata in {args.config_output or args.config}")
    for name in sorted(result.rms_errors):
        print(f"  {name}: {result.rms_errors[name] * 1000:.1f}mm RMSE")
    print(f"RMSE totale: {result.rms_total * 1000:.1f}mm")
    if weak_overlap_pairs:
        links = ", ".join(
            f"{left}<->{right} via ID {marker_id}" for left, right, marker_id in weak_overlap_pairs
        )
        print(
            "ATTENZIONE: sovrapposizioni basate su un solo marker; "
            f"aggiungerne un secondo migliora la precisione: {links}"
        )
    quality_warning_m = max(args.uncertainty_m * 2, marker_size_m * 0.2)
    if result.rms_total > quality_warning_m:
        print(
            "ATTENZIONE: RMSE elevato per una mappa reference; aggiungere marker nelle "
            "sovrapposizioni e ripetere l'acquisizione"
        )
    if result.detached_images:
        print(
            "ATTENZIONE: immagini non collegabili agli anchor (troppi pochi marker "
            f"in comune): {result.detached_images}"
        )
    if result.dropped_markers:
        print(
            f"ATTENZIONE: marker esclusi (visibili solo in immagini scollegate): "
            f"{result.dropped_markers}"
        )
    missing_anchors = sorted(set(anchors) - set(result.used_anchors))
    if missing_anchors:
        print(f"ATTENZIONE: anchor non rilevati: {missing_anchors}")
    if not anchors:
        print("ATTENZIONE: senza anchor il frame world e arbitrario")


if __name__ == "__main__":
    reference_stitcher_main()
