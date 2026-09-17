"""Typed views over the MQTT payloads the coordinator and the nodes publish.

The panel used to re-read every field with ``body.get(...)`` at the point of use,
and to decide what a message *was* twice: once by topic suffix and once by testing
for ``"/pose/"``. Classification and decoding happen here instead, so a consumer
receives a value object and never a raw dict.

Decoding is lenient on purpose: a malformed payload yields ``None`` rather than an
exception, because the panel must survive whatever a half-upgraded node publishes.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

EVENT_ENVELOPE_KEYS = frozenset({"code", "message", "severity", "timestamp"})


class MessageKind(StrEnum):
    """What a topic says a payload is, independently of its contents."""

    METRICS = "metrics"
    EVENT = "event"
    STATUS = "status"
    POSE = "pose"
    UNKNOWN = "unknown"


def classify(topic: str) -> MessageKind:
    """Map a topic to its kind. ``<base>/pose/<tag_id>`` is the only nested form."""
    if "/pose/" in topic:
        return MessageKind.POSE
    if topic.endswith("/metrics"):
        return MessageKind.METRICS
    if topic.endswith("/event"):
        return MessageKind.EVENT
    if topic.endswith("/status"):
        return MessageKind.STATUS
    return MessageKind.UNKNOWN


@dataclass(frozen=True)
class CameraMetrics:
    """What the coordinator reports about one camera it is fusing."""

    online: bool
    observations_received: int | None = None
    calibrated: bool | None = None
    age_ms: float | None = None
    note: str = ""

    @classmethod
    def from_body(cls, body: object) -> CameraMetrics:
        if not isinstance(body, Mapping):
            return cls(online=False)
        return cls(
            online=bool(body.get("online", False)),
            observations_received=_as_int(body.get("observations_received")),
            calibrated=_as_bool(body.get("calibrated")),
            age_ms=_as_float(body.get("age_ms")),
            note=str(body.get("note", "")),
        )


@dataclass(frozen=True)
class CoordinatorMetrics:
    """One ``role: coordinator`` metrics report."""

    poses_published: int = 0
    tracked_tags: tuple[int, ...] = ()
    cameras: Mapping[str, CameraMetrics] = field(default_factory=dict)
    body: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Mapping[str, object]) -> CoordinatorMetrics:
        raw_cameras = body.get("cameras")
        cameras: dict[str, CameraMetrics] = {}
        if isinstance(raw_cameras, Mapping):
            for camera_id, metrics in raw_cameras.items():
                cameras[str(camera_id)] = CameraMetrics.from_body(metrics)
        raw_tags = body.get("tracked_tags")
        tags = tuple(_as_int(tag) or 0 for tag in raw_tags) if isinstance(raw_tags, list) else ()
        return cls(
            poses_published=_as_int(body.get("poses_published")) or 0,
            tracked_tags=tags,
            cameras=cameras,
            body=body,
        )


@dataclass(frozen=True)
class NodeMetrics:
    """One ``role: node`` metrics report, always scoped to a single camera."""

    camera_id: str
    observations_published: int | None = None
    excluded_for_drift: bool = False
    body: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Mapping[str, object]) -> NodeMetrics | None:
        camera_id = body.get("camera_id")
        if not isinstance(camera_id, str):
            return None
        return cls(
            camera_id=camera_id,
            observations_published=_as_int(body.get("observations_published")),
            excluded_for_drift=bool(body.get("excluded_for_drift", False)),
            body=body,
        )


@dataclass(frozen=True)
class DeploymentEvent:
    """A structured event such as MISSING_CALIBRATION or CALIBRATION_DRIFT."""

    code: str
    message: str
    severity: str
    context: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Mapping[str, object]) -> DeploymentEvent:
        return cls(
            code=str(body.get("code", "EVENT")),
            message=str(body.get("message", "")),
            severity=str(body.get("severity", "info")).upper(),
            context={
                key: value for key, value in body.items() if key not in EVENT_ENVELOPE_KEYS
            },
        )

    def context_suffix(self) -> str:
        """The compact JSON tail appended to a rendered event line, or ``""``."""
        if not self.context:
            return ""
        return " " + json.dumps(dict(self.context), separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ServerStatus:
    """The coordinator's own liveness announcement."""

    online: bool | None
    reason: str
    body: Mapping[str, object] = field(default_factory=dict)

    @classmethod
    def from_body(cls, body: Mapping[str, object]) -> ServerStatus:
        return cls(
            online=_as_bool(body.get("online")),
            reason=str(body.get("reason", "")),
            body=body,
        )


@dataclass(frozen=True)
class PoseUpdate:
    """One fused pose as published on ``<base>/pose/<tag_id>``."""

    tag_id: int
    x_m: float
    y_m: float
    z_m: float
    heading_rad: float
    quality: float
    predicted: bool
    cameras: tuple[str, ...]

    @classmethod
    def from_body(cls, body: Mapping[str, object]) -> PoseUpdate | None:
        position = body.get("position_m")
        if not isinstance(position, Mapping):
            return None
        try:
            tag_id = int(body["tag_id"])  # type: ignore[arg-type]
            x_m = float(position["x"])  # type: ignore[arg-type]
            y_m = float(position["y"])  # type: ignore[arg-type]
            z_m = float(position.get("z", 0.0))  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            return None
        cameras = body.get("visible_by")
        return cls(
            tag_id=tag_id,
            x_m=x_m,
            y_m=y_m,
            z_m=z_m,
            heading_rad=heading_from_body(body),
            quality=float(body.get("quality", 0.0) or 0.0),  # type: ignore[arg-type]
            predicted=bool(body.get("predicted", False)),
            cameras=tuple(str(camera) for camera in cameras) if isinstance(cameras, list) else (),
        )


DeploymentMessage = (
    CoordinatorMetrics | NodeMetrics | DeploymentEvent | ServerStatus | PoseUpdate
)


def parse(topic: str, body: Mapping[str, object]) -> DeploymentMessage | None:
    """Decode one message, or ``None`` when the topic or the payload is unusable."""
    kind = classify(topic)
    if kind is MessageKind.POSE:
        return PoseUpdate.from_body(body)
    if kind is MessageKind.EVENT:
        return DeploymentEvent.from_body(body)
    if kind is MessageKind.STATUS:
        return ServerStatus.from_body(body)
    if kind is MessageKind.METRICS:
        role = body.get("role")
        if role == "coordinator":
            return CoordinatorMetrics.from_body(body)
        if role == "node":
            return NodeMetrics.from_body(body)
    return None


def heading_from_body(body: Mapping[str, object]) -> float:
    """Yaw in radians, preferring the published Euler angle over the quaternion."""
    euler = body.get("euler_deg")
    if isinstance(euler, Mapping) and "yaw" in euler:
        try:
            return math.radians(float(euler["yaw"]))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
    quaternion = body.get("orientation_xyzw")
    if isinstance(quaternion, Mapping):
        try:
            x, y, z, w = (float(quaternion[axis]) for axis in ("x", "y", "z", "w"))
        except (KeyError, TypeError, ValueError):
            return 0.0
        return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return 0.0


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_bool(value: object) -> bool | None:
    return None if value is None else bool(value)
