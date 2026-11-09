"""ArUco marker detection in image coordinates and camera-frame PnP estimation."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.aruco import create_marker_detector
from ..core.config import ArucoConfig, CameraCalibration
from ..core.geometry import invert_transform, marker_object_points


@dataclass
class TagObservation:
    tag_id: int
    camera_id: str
    monotonic_ns: int
    utc_ns: int
    corners: NDArray[np.float64]
    camera_from_tag: NDArray[np.float64]
    world_from_tag: NDArray[np.float64]
    world_from_camera: NDArray[np.float64]
    camera_matrix: NDArray[np.float64]
    distortion: NDArray[np.float64]
    object_points: NDArray[np.float64]
    reprojection_error_px: float
    marker_side_px: float
    candidate_world_from_tags: tuple[NDArray[np.float64], ...] = ()
    candidate_camera_from_tags: tuple[NDArray[np.float64], ...] = ()


class MarkerDetector:
    def __init__(self, aruco: ArucoConfig, calibrations: dict[str, CameraCalibration]) -> None:
        self.detector = create_marker_detector(aruco)
        self.marker_sizes = {marker.id: marker.size_m for marker in aruco.mobile_markers}
        self.reference_ids = {marker.id for marker in aruco.reference_markers}
        self.auto_mobile_markers = aruco.auto_mobile_markers
        self.ignored_ids = set(aruco.auto_mobile_markers.ignored_ids)
        self.calibrations = calibrations

    def detect(
        self,
        camera_id: str,
        image: NDArray[np.uint8],
        monotonic_ns: int,
        utc_ns: int,
    ) -> tuple[list[TagObservation], tuple, NDArray[np.int32] | None]:
        calibration = self.calibrations.get(camera_id)
        if calibration is None or calibration.world_from_camera is None:
            return [], (), None
        corners, ids, rejected = self.detector.detectMarkers(image)
        if ids is None:
            return [], corners, ids
        camera_matrix = np.asarray(calibration.camera_matrix, dtype=np.float64)
        distortion = np.asarray(calibration.distortion, dtype=np.float64)
        world_from_camera = np.asarray(calibration.world_from_camera, dtype=np.float64)
        observations: list[TagObservation] = []
        for marker_corners, marker_id_raw in zip(corners, ids.reshape(-1), strict=True):
            marker_id = int(marker_id_raw)
            if marker_id in self.reference_ids:
                continue
            size = self.marker_sizes.get(marker_id)
            if (
                size is None
                and self.auto_mobile_markers.enabled
                and marker_id not in self.ignored_ids
            ):
                size = self.auto_mobile_markers.default_size_m
            if size is None:
                continue
            image_points = np.asarray(marker_corners, dtype=np.float64).reshape(4, 2)
            object_points = marker_object_points(size)
            result = cv2.solvePnPGeneric(
                object_points,
                image_points,
                camera_matrix,
                distortion,
                flags=cv2.SOLVEPNP_IPPE_SQUARE,
            )
            if not result[0] or not result[1]:
                continue
            rvecs, tvecs = result[1], result[2]
            errors = result[3] if len(result) > 3 and result[3] is not None else None
            candidates: list[tuple[float, NDArray[np.float64], NDArray[np.float64]]] = []
            for index, (rvec, tvec) in enumerate(zip(rvecs, tvecs, strict=True)):
                if float(np.asarray(tvec).reshape(3)[2]) <= 0:
                    continue
                rotation, _ = cv2.Rodrigues(rvec)
                camera_from_tag = np.eye(4, dtype=np.float64)
                camera_from_tag[:3, :3] = rotation
                camera_from_tag[:3, 3] = np.asarray(tvec).reshape(3)
                if errors is not None:
                    error = float(np.asarray(errors).reshape(-1)[index])
                else:
                    projected, _ = cv2.projectPoints(
                        object_points, rvec, tvec, camera_matrix, distortion
                    )
                    residual = projected.reshape(4, 2) - image_points
                    error = float(np.sqrt(np.mean(np.sum(residual * residual, axis=1))))
                candidates.append((error, camera_from_tag, world_from_camera @ camera_from_tag))
            if not candidates:
                continue
            error, camera_from_tag, world_from_tag = min(candidates, key=lambda item: item[0])
            side_lengths = [
                np.linalg.norm(image_points[(index + 1) % 4] - image_points[index])
                for index in range(4)
            ]
            observations.append(
                TagObservation(
                    tag_id=marker_id,
                    camera_id=camera_id,
                    monotonic_ns=monotonic_ns,
                    utc_ns=utc_ns,
                    corners=image_points,
                    camera_from_tag=camera_from_tag,
                    world_from_tag=world_from_tag,
                    world_from_camera=world_from_camera,
                    camera_matrix=camera_matrix,
                    distortion=distortion,
                    object_points=object_points,
                    reprojection_error_px=error,
                    marker_side_px=float(np.mean(side_lengths)),
                    candidate_world_from_tags=tuple(item[2] for item in candidates),
                    candidate_camera_from_tags=tuple(item[1] for item in candidates),
                )
            )
        return observations, corners, ids


def camera_from_world(observation: TagObservation) -> NDArray[np.float64]:
    return invert_transform(observation.world_from_camera)
