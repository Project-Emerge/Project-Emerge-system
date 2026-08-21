"""Compact wire format and serialization for camera node observations."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import numpy as np

from ..core.config import CameraCalibration
from ..core.geometry import marker_object_points
from ..pipeline.detection import TagObservation

LOGGER = logging.getLogger(__name__)
OBSERVATION_SCHEMA_VERSION = 1


def observation_item(observation: TagObservation) -> dict:
    """Serialize one detected marker into the node-to-server wire format."""
    return {
        "tag_id": observation.tag_id,
        "size_m": float(
            np.linalg.norm(observation.object_points[1] - observation.object_points[0])
        ),
        "corners": observation.corners.tolist(),
        "camera_from_tag": observation.camera_from_tag[:3, :].tolist(),
        "candidates": [
            candidate[:3, :].tolist()
            for candidate in observation.candidate_camera_from_tags
        ],
        "reprojection_error_px": observation.reprojection_error_px,
        "marker_side_px": observation.marker_side_px,
    }


def observations_payload(
    camera_id: str,
    utc_ns: int,
    observations: Sequence[TagObservation | dict],
) -> dict:
    """Build the stable payload published on ``observations/<camera>``."""
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "camera_id": camera_id,
        "utc_ns": utc_ns,
        "observations": [
            item if isinstance(item, dict) else observation_item(item)
            for item in observations
        ],
    }


def reconstruct_observations(
    body: dict,
    calibrations: Mapping[str, CameraCalibration],
    utc_to_monotonic_ns: int,
) -> tuple[list[TagObservation], str | None]:
    """Rebuild observations received from a camera node using local calibration."""
    try:
        camera_id = str(body["camera_id"])
        version = body.get("schema_version", OBSERVATION_SCHEMA_VERSION)
        if version != OBSERVATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {version}")
        calibration = calibrations.get(camera_id)
        if calibration is None or calibration.world_from_camera is None:
            return [], "missing_calibration"
        utc_ns = int(body["utc_ns"])
        camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
        distortion = np.asarray(calibration.distortion, dtype=np.float64)
        world_from_camera = np.asarray(calibration.world_from_camera, dtype=np.float64)
        observations: list[TagObservation] = []
        for item in body.get("observations", []):
            size_m = float(item["size_m"])
            corners = np.asarray(item["corners"], dtype=np.float64).reshape(4, 2)
            camera_from_tag = np.eye(4, dtype=np.float64)
            camera_from_tag[:3, :] = np.asarray(
                item["camera_from_tag"], dtype=np.float64
            ).reshape(3, 4)
            candidates: list[np.ndarray] = []
            for candidate in item.get("candidates", []):
                matrix = np.eye(4, dtype=np.float64)
                matrix[:3, :] = np.asarray(candidate, dtype=np.float64).reshape(3, 4)
                candidates.append(world_from_camera @ matrix)
            observations.append(
                TagObservation(
                    tag_id=int(item["tag_id"]),
                    camera_id=camera_id,
                    monotonic_ns=utc_ns + utc_to_monotonic_ns,
                    utc_ns=utc_ns,
                    corners=corners,
                    camera_from_tag=camera_from_tag,
                    world_from_tag=world_from_camera @ camera_from_tag,
                    world_from_camera=world_from_camera,
                    camera_matrix=camera_matrix,
                    distortion=distortion,
                    object_points=marker_object_points(size_m),
                    reprojection_error_px=float(item.get("reprojection_error_px", 0.0)),
                    marker_side_px=float(item.get("marker_side_px", 0.0)),
                    candidate_world_from_tags=tuple(candidates),
                )
            )
        return observations, None
    except (KeyError, TypeError, ValueError) as error:
        LOGGER.warning("observation message rejected: %s", error)
        return [], "invalid_message"
