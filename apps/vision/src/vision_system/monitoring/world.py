"""World model: fused poses, their trails, and the static arena scene."""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from ..core.config import AppConfig, CameraCalibration

# A pose older than this is drawn as stale: the fusion publishes several times per
# second, so half a second of silence already means "this tag is not being seen".
STALE_POSE_NS = 1_500_000_000
WORLD_MARGIN_M = 0.6


@dataclass(frozen=True)
class TrackedTag:
    """One fused pose as published on ``<base>/pose/<tag_id>``."""

    tag_id: int
    x_m: float
    y_m: float
    z_m: float
    heading_rad: float
    quality: float
    predicted: bool
    cameras: tuple[str, ...]
    updated_ns: int

    def stale(self, now_ns: int, timeout_ns: int = STALE_POSE_NS) -> bool:
        return now_ns - self.updated_ns >= timeout_ns


def _yaw_from_payload(body: dict) -> float:
    euler = body.get("euler_deg")
    if isinstance(euler, dict) and "yaw" in euler:
        try:
            return math.radians(float(euler["yaw"]))
        except (TypeError, ValueError):
            return 0.0
    quaternion = body.get("orientation_xyzw")
    if isinstance(quaternion, dict):
        try:
            x, y, z, w = (float(quaternion[axis]) for axis in ("x", "y", "z", "w"))
        except (KeyError, TypeError, ValueError):
            return 0.0
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return 0.0


class WorldModel:
    """Tag positions decoded from the MQTT pose stream, plus the static scene.

    The panel never recomputes fusion: it draws exactly the poses the coordinator
    publishes, which is what makes it a usable cross-check of a remote server.
    """

    def __init__(self, trail_seconds: float = 3.0) -> None:
        self.trail_seconds = trail_seconds
        self.tags_by_id: dict[int, TrackedTag] = {}
        self.trails: dict[int, deque[tuple[int, tuple[float, float]]]] = defaultdict(deque)
        self.references: list[tuple[int, float, float]] = []
        self.cameras: dict[str, tuple[float, float, float]] = {}

    def update_scene(
        self, config: AppConfig | None, calibrations: Mapping[str, CameraCalibration]
    ) -> None:
        """Refresh the static backdrop: reference markers and calibrated camera poses."""
        if config is not None:
            self.trail_seconds = config.debug.trail_seconds or self.trail_seconds
            self.references = [
                (marker.id, float(marker.position_m[0]), float(marker.position_m[1]))
                for marker in config.aruco.reference_markers
            ]
        cameras: dict[str, tuple[float, float, float]] = {}
        for camera_id, calibration in calibrations.items():
            if calibration.world_from_camera is None:
                continue
            transform = np.asarray(calibration.world_from_camera, dtype=float)
            forward = transform[:3, :3] @ np.array([0.0, 0.0, 1.0])
            cameras[camera_id] = (
                float(transform[0, 3]),
                float(transform[1, 3]),
                math.atan2(float(forward[1]), float(forward[0])),
            )
        self.cameras = cameras

    def apply_pose(self, body: dict, now_ns: int | None = None) -> TrackedTag | None:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        position = body.get("position_m")
        if not isinstance(position, dict):
            return None
        try:
            tag_id = int(body["tag_id"])
            x_m = float(position["x"])
            y_m = float(position["y"])
            z_m = float(position.get("z", 0.0))
        except (KeyError, TypeError, ValueError):
            return None
        cameras = body.get("visible_by")
        tag = TrackedTag(
            tag_id=tag_id,
            x_m=x_m,
            y_m=y_m,
            z_m=z_m,
            heading_rad=_yaw_from_payload(body),
            quality=float(body.get("quality", 0.0) or 0.0),
            predicted=bool(body.get("predicted", False)),
            cameras=tuple(str(camera) for camera in cameras) if isinstance(cameras, list) else (),
            updated_ns=now_ns,
        )
        self.tags_by_id[tag_id] = tag
        trail = self.trails[tag_id]
        trail.append((now_ns, (x_m, y_m)))
        self._expire_trail(trail, now_ns)
        return tag

    def _expire_trail(self, trail: deque[tuple[int, tuple[float, float]]], now_ns: int) -> None:
        horizon_ns = int(self.trail_seconds * 1e9)
        while trail and now_ns - trail[0][0] > horizon_ns:
            trail.popleft()

    def tags(self, now_ns: int | None = None, keep_ns: int = 5 * STALE_POSE_NS) -> list[TrackedTag]:
        """Tags seen recently enough to be worth drawing, newest position first."""
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        for tag_id in list(self.tags_by_id):
            if now_ns - self.tags_by_id[tag_id].updated_ns > keep_ns:
                del self.tags_by_id[tag_id]
                self.trails.pop(tag_id, None)
        return sorted(self.tags_by_id.values(), key=lambda tag: tag.tag_id)

    def reset(self) -> None:
        """Forget every tracked tag, e.g. after switching broker or configuration."""
        self.tags_by_id.clear()
        self.trails.clear()

    def trail(self, tag_id: int, now_ns: int | None = None) -> list[tuple[float, float]]:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        trail = self.trails.get(tag_id)
        if not trail:
            return []
        self._expire_trail(trail, now_ns)
        return [point for _, point in trail]

    def bounds(self, margin_m: float = WORLD_MARGIN_M) -> tuple[float, float, float, float]:
        """World rectangle to draw: references, cameras and tags always fit inside."""
        xs = [0.0]
        ys = [0.0]
        for _, x_m, y_m in self.references:
            xs.append(x_m)
            ys.append(y_m)
        for x_m, y_m, _ in self.cameras.values():
            xs.append(x_m)
            ys.append(y_m)
        for tag in self.tags_by_id.values():
            xs.append(tag.x_m)
            ys.append(tag.y_m)
        return (
            min(xs) - margin_m,
            min(ys) - margin_m,
            max(xs) + margin_m,
            max(ys) + margin_m,
        )
