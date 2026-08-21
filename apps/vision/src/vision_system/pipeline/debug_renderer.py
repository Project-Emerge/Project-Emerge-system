"""Debug visualization for camera mosaics and 2D world-view tag trajectories."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

import cv2
import numpy as np
from numpy.typing import NDArray

from ..core.config import AppConfig, CameraCalibration
from .capture import Frame
from .detection import TagObservation
from .fusion import FusedPose

LOGGER = logging.getLogger(__name__)


class DebugRenderer:
    def __init__(self, config: AppConfig, calibrations: dict[str, CameraCalibration]) -> None:
        self.config = config
        self.calibrations = calibrations
        self.enabled = True
        self._shown_mosaic = False
        self._shown_world = False
        self.trails: dict[int, deque[tuple[float, NDArray[np.float64]]]] = defaultdict(deque)

    def update(self, config: AppConfig, calibrations: dict[str, CameraCalibration]) -> None:
        self.config = config
        self.calibrations = calibrations

    def render(
        self,
        frames: dict[str, Frame],
        observations: dict[str, list[TagObservation]],
        poses: list[FusedPose],
    ) -> None:
        if not self.enabled:
            return
        try:
            if self.config.debug.mosaic:
                cv2.imshow("VisionSystem - mosaic", self._mosaic(frames, observations))
                self._shown_mosaic = True
            elif self._shown_mosaic:
                cv2.destroyWindow("VisionSystem - mosaic")
                self._shown_mosaic = False
            if self.config.debug.world_view:
                cv2.imshow("VisionSystem - world", self._world(poses))
                self._shown_world = True
            elif self._shown_world:
                cv2.destroyWindow("VisionSystem - world")
                self._shown_world = False
            cv2.waitKey(1)
        except cv2.error as error:
            LOGGER.error("debug windows unavailable; disabling GUI: %s", error)
            self.enabled = False

    def _mosaic(
        self, frames: dict[str, Frame], observations: dict[str, list[TagObservation]]
    ) -> NDArray[np.uint8]:
        tiles: list[NDArray[np.uint8]] = []
        for camera in self.config.cameras:
            frame = frames.get(camera.id)
            if frame is None:
                tile = np.zeros((360, 640, 3), dtype=np.uint8)
                cv2.putText(
                    tile,
                    f"{camera.id}: OFFLINE",
                    (30, 180),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 0, 255),
                    2,
                )
            else:
                calibration = self.calibrations.get(camera.id)
                tile = frame.image.copy()
                for observation in observations.get(camera.id, []):
                    corners = observation.corners.astype(np.int32).reshape((-1, 1, 2))
                    cv2.polylines(tile, [corners], True, (0, 255, 0), 3)
                    rvec, _ = cv2.Rodrigues(observation.camera_from_tag[:3, :3])
                    cv2.drawFrameAxes(
                        tile,
                        observation.camera_matrix,
                        observation.distortion,
                        rvec,
                        observation.camera_from_tag[:3, 3],
                        float(
                            np.linalg.norm(
                                observation.object_points[1] - observation.object_points[0]
                            )
                        ),
                        2,
                    )
                    point = tuple(corners[0, 0])
                    cv2.putText(
                        tile,
                        f"ID {observation.tag_id} e={observation.reprojection_error_px:.2f}px",
                        point,
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.7,
                        (0, 255, 0),
                        2,
                    )
                if calibration:
                    tile = cv2.undistort(
                        tile,
                        np.asarray(calibration.camera_matrix),
                        np.asarray(calibration.distortion),
                    )
            tile = cv2.resize(tile, (640, 360), interpolation=cv2.INTER_AREA)
            cv2.putText(tile, camera.id, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 220, 0), 2)
            tiles.append(tile)
        columns = 2 if len(tiles) > 1 else 1
        rows = [
            np.hstack(tiles[start : start + columns])
            for start in range(0, len(tiles), columns)
        ]
        if len(rows) > 1 and rows[-1].shape[1] != rows[0].shape[1]:
            padding = np.zeros_like(rows[0])[:, : rows[0].shape[1] - rows[-1].shape[1]]
            rows[-1] = np.hstack((rows[-1], padding))
        return np.vstack(rows)

    def _world(self, poses: list[FusedPose]) -> NDArray[np.uint8]:
        image = np.full((800, 1000, 3), 245, dtype=np.uint8)
        points: list[NDArray[np.float64]] = [np.zeros(3)]
        for calibration in self.calibrations.values():
            if calibration.world_from_camera is not None:
                points.append(np.asarray(calibration.world_from_camera)[:3, 3])
        points.extend(
            np.asarray(reference.position_m) for reference in self.config.aruco.reference_markers
        )
        points.extend(pose.position_m for pose in poses)
        xy = np.array([point[:2] for point in points])
        minimum = np.min(xy, axis=0) - 0.5
        maximum = np.max(xy, axis=0) + 0.5
        extent = np.maximum(maximum - minimum, 1.0)
        scale = min(900 / extent[0], 700 / extent[1])

        def pixel(point) -> tuple[int, int]:
            normalized = (np.asarray(point)[:2] - minimum) * scale
            return int(50 + normalized[0]), int(750 - normalized[1])

        origin = pixel((0, 0, 0))
        cv2.arrowedLine(image, origin, pixel((0.5, 0, 0)), (0, 0, 255), 2)
        cv2.arrowedLine(image, origin, pixel((0, 0.5, 0)), (0, 180, 0), 2)
        cv2.putText(image, "X", pixel((0.55, 0, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        cv2.putText(image, "Y", pixel((0, 0.55, 0)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 180, 0), 2)
        for camera_id, calibration in self.calibrations.items():
            if calibration.world_from_camera is None:
                continue
            transform = np.asarray(calibration.world_from_camera)
            center = transform[:3, 3]
            forward = transform[:3, :3] @ np.array([0, 0, 0.4]) + center
            cv2.circle(image, pixel(center), 8, (255, 80, 0), -1)
            cv2.arrowedLine(image, pixel(center), pixel(forward), (255, 80, 0), 2)
            cv2.putText(
                image,
                camera_id,
                pixel(center + np.array([0.05, 0.05, 0])),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (80, 40, 0),
                1,
            )
        for reference in self.config.aruco.reference_markers:
            cv2.drawMarker(
                image, pixel(reference.position_m), (130, 0, 130), cv2.MARKER_DIAMOND, 14, 2
            )
            cv2.putText(
                image,
                f"R{reference.id}",
                pixel(reference.position_m),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (130, 0, 130),
                1,
            )
        now = time.monotonic()
        for pose in poses:
            self.trails[pose.tag_id].append((now, pose.position_m.copy()))
        for tag_id, trail in self.trails.items():
            while trail and now - trail[0][0] > self.config.debug.trail_seconds:
                trail.popleft()
            if len(trail) > 1:
                cv2.polylines(
                    image,
                    [np.array([pixel(point) for _, point in trail])],
                    False,
                    (170, 170, 170),
                    1,
                )
            if not trail:
                continue
            pose = next((item for item in poses if item.tag_id == tag_id), None)
            if pose is None:
                continue
            center = pose.position_m
            direction = pose.world_from_tag[:3, :3] @ np.array([0.25, 0, 0]) + center
            color = (0, int(255 * pose.quality), int(255 * (1 - pose.quality)))
            cv2.circle(image, pixel(center), 8, color, -1)
            cv2.arrowedLine(image, pixel(center), pixel(direction), color, 2)
            cv2.putText(
                image,
                f"ID {tag_id}",
                pixel(center + np.array([0.05, 0.05, 0])),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )
        return image
