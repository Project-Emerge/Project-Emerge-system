"""Tkinter control panel that launches the fusion server and watches a distributed deployment.

The distributed runtime is a set of independent processes (one ``vision-node`` per
camera PC plus one ``vision-server``), so the usual debugging question is not "what
does the fusion compute" but "who is actually talking to whom". This panel starts and
stops the local ``vision-server`` process, streams its console output, and renders the
roster exactly as the two sides see it: what each node claims to publish and what the
coordinator claims to receive.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import signal
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt

from ...core.config import AppConfig, CameraCalibration, initial_config
from ...pipeline.calibration_store import CalibrationStore
from ...transport.diagnostics import configure_diagnostics
from ...transport.mqtt import MQTT_KEEPALIVE_S, MqttSettings
from .supervisor import (
    DEFAULT_CACHE,
    DEFAULT_CALIBRATIONS,
    MAX_LOG_LINES,
    STOP_TIMEOUT_S,
    ServerLaunchOptions,
    ServerProcess,
    build_server_command,
    build_server_environment,
)

__all__ = [
    "DEFAULT_CACHE",
    "DEFAULT_CALIBRATIONS",
    "MAX_LOG_LINES",
    "PRESENCE_TIMEOUT_NS",
    "STALE_POSE_NS",
    "STOP_TIMEOUT_S",
    "VIEWPORT_QUANTUM_M",
    "DeploymentStatus",
    "ServerLaunchOptions",
    "ServerProcess",
    "StatusMonitor",
    "WorldModel",
    "build_server_command",
    "build_server_environment",
    "fit_viewport",
    "roster_from_config",
    "server_gui_main",
]

# Nodes and coordinator publish metrics once per second: three missed reports is a
# generous "this process is gone" threshold that survives a hiccup on the broker.
PRESENCE_TIMEOUT_NS = 3_000_000_000
REFRESH_MS = 250
# A pose older than this is drawn as stale: the fusion publishes several times per
# second, so half a second of silence already means "this tag is not being seen".
STALE_POSE_NS = 1_500_000_000
CALIBRATION_POLL_S = 2.0
WORLD_MARGIN_M = 0.6
# The drawn viewport snaps to this grid so that a moving tag does not rescale
# the whole scene on every frame.
VIEWPORT_QUANTUM_M = 0.5




@dataclass(frozen=True)
class CameraRow:
    """One roster line as seen from both ends of the distributed pipeline."""

    camera_id: str
    node_online: bool
    node_observations: int | None
    server_online: bool
    observations_received: int | None
    age_ms: float | None
    calibrated: bool | None
    note: str


class DeploymentStatus:
    """Aggregates coordinator and node metrics into the roster table."""

    def __init__(self, camera_ids: Sequence[str] = ()) -> None:
        self.expected_camera_ids = list(camera_ids)
        self.coordinator: dict = {}
        self.coordinator_seen_ns: int | None = None
        self.nodes: dict[str, dict] = {}
        self.node_seen_ns: dict[str, int] = {}
        self.server_status: dict = {}
        self.poses_published = 0
        self.tracked_tags: list[int] = []

    def apply(self, topic: str, body: dict, now_ns: int | None = None) -> str | None:
        """Consume one MQTT message; returns a log line when the message is noteworthy."""
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        if topic.endswith("/metrics"):
            return self._apply_metrics(body, now_ns)
        if topic.endswith("/event"):
            return self._apply_event(body)
        if topic.endswith("/status"):
            self.server_status = body
            reason = body.get("reason", "")
            return f"[status] online={body.get('online')} {reason}".strip()
        return None

    def _apply_metrics(self, body: dict, now_ns: int) -> str | None:
        role = body.get("role")
        if role == "coordinator":
            self.coordinator = body
            self.coordinator_seen_ns = now_ns
            self.poses_published = int(body.get("poses_published", 0))
            self.tracked_tags = [int(tag) for tag in body.get("tracked_tags", [])]
            for camera_id in body.get("cameras", {}):
                self._register(camera_id)
            return None
        if role == "node":
            camera_id = body.get("camera_id")
            if not isinstance(camera_id, str):
                return None
            self._register(camera_id)
            self.nodes[camera_id] = body
            self.node_seen_ns[camera_id] = now_ns
            return None
        return None

    def _apply_event(self, body: dict) -> str:
        severity = str(body.get("severity", "info")).upper()
        code = body.get("code", "EVENT")
        message = body.get("message", "")
        context = {
            key: value
            for key, value in body.items()
            if key not in {"code", "message", "severity", "timestamp"}
        }
        suffix = f" {json.dumps(context, separators=(',', ':'), default=str)}" if context else ""
        return f"[{severity}] {code}: {message}{suffix}"

    def _register(self, camera_id: str) -> None:
        if camera_id not in self.expected_camera_ids:
            self.expected_camera_ids.append(camera_id)

    def coordinator_online(self, now_ns: int | None = None) -> bool:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        return (
            self.coordinator_seen_ns is not None
            and now_ns - self.coordinator_seen_ns < PRESENCE_TIMEOUT_NS
        )

    def rows(self, now_ns: int | None = None) -> list[CameraRow]:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        cameras = self.coordinator.get("cameras", {})
        rows: list[CameraRow] = []
        for camera_id in self.expected_camera_ids:
            server_view = cameras.get(camera_id, {})
            node_view = self.nodes.get(camera_id, {})
            seen_ns = self.node_seen_ns.get(camera_id)
            node_online = seen_ns is not None and now_ns - seen_ns < PRESENCE_TIMEOUT_NS
            server_online = bool(server_view.get("online", False)) and self.coordinator_online(
                now_ns
            )
            calibrated = server_view.get("calibrated")
            notes: list[str] = []
            if node_view.get("excluded_for_drift"):
                notes.append("drift: ricalibrare")
            if calibrated is False:
                notes.append("calibrazione assente sul server")
            if node_online and not server_online:
                notes.append("nodo attivo ma nessuna osservazione al server")
            if not node_online and server_online:
                notes.append("osservazioni senza metriche del nodo")
            rows.append(
                CameraRow(
                    camera_id=camera_id,
                    node_online=node_online,
                    node_observations=node_view.get("observations_published"),
                    server_online=server_online,
                    observations_received=server_view.get("observations_received"),
                    age_ms=server_view.get("age_ms"),
                    calibrated=calibrated,
                    note="; ".join(notes),
                )
            )
        return rows


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


class StatusMonitor:
    """Read-only MQTT listener: never publishes, so it cannot disturb the deployment."""

    def __init__(
        self,
        base_topic: str,
        settings: MqttSettings | None = None,
        client_factory: Callable[[str], mqtt.Client] | None = None,
    ) -> None:
        self.base_topic = base_topic
        self.settings = settings or MqttSettings.from_environment()
        self.connected = threading.Event()
        self.error: str | None = None
        self.messages: queue.SimpleQueue[tuple[str, dict]] = queue.SimpleQueue()
        client_id = f"vision-gui-{uuid.uuid4().hex[:10]}"
        self.client = (
            client_factory(client_id)
            if client_factory
            else mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                reconnect_on_failure=True,
            )
        )
        if self.settings.username:
            self.client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.tls:
            self.client.tls_set()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._loop_started = False

    def topics(self) -> list[str]:
        return [
            f"{self.base_topic}/metrics",
            f"{self.base_topic}/event",
            f"{self.base_topic}/status",
            f"{self.base_topic}/pose/+",
        ]

    def start(self) -> None:
        try:
            self.client.connect_async(
                self.settings.host, self.settings.port, keepalive=MQTT_KEEPALIVE_S
            )
            self.client.loop_start()
            self._loop_started = True
        except OSError as error:
            self.error = str(error)

    def stop(self) -> None:
        try:
            self.client.disconnect()
        finally:
            if self._loop_started:
                self.client.loop_stop()
                self._loop_started = False
            self.connected.clear()

    def drain(self, limit: int = 500) -> list[tuple[str, dict]]:
        received: list[tuple[str, dict]] = []
        while len(received) < limit:
            try:
                received.append(self.messages.get_nowait())
            except queue.Empty:
                break
        return received

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self.error = f"connessione MQTT rifiutata: {reason_code}"
            return
        self.error = None
        self.connected.set()
        for topic in self.topics():
            client.subscribe(topic, qos=0)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected.clear()

    def _on_message(self, client, userdata, message) -> None:
        try:
            body = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(body, dict):
            self.messages.put((message.topic, body))


def roster_from_config(config_path: Path | None, cache_path: Path) -> tuple[AppConfig, list[str]]:
    config = initial_config(config_path, cache_path)
    return config, [camera.id for camera in config.cameras]


def _format_flag(value: bool) -> str:
    return "●" if value else "○"


def _format_optional(value: object) -> str:
    return "—" if value is None else str(value)


def fit_viewport(
    current: tuple[float, float, float, float] | None,
    content: tuple[float, float, float, float],
    quantum: float = VIEWPORT_QUANTUM_M,
) -> tuple[float, float, float, float]:
    """Pick a viewport that stays put while the tracked tags move.

    Deriving the extent from the tag positions on every frame rescales the whole
    scene a few times per second, which reads as flicker: the viewport is kept as
    long as the content still fits and is not absurdly smaller than the view.
    """
    min_x, min_y, max_x, max_y = content
    candidate = (
        math.floor(min_x / quantum) * quantum,
        math.floor(min_y / quantum) * quantum,
        max(math.ceil(max_x / quantum) * quantum, math.floor(min_x / quantum) * quantum + quantum),
        max(math.ceil(max_y / quantum) * quantum, math.floor(min_y / quantum) * quantum + quantum),
    )
    if current is None:
        return candidate
    fits = (
        current[0] <= min_x
        and current[1] <= min_y
        and current[2] >= max_x
        and current[3] >= max_y
    )
    oversized = (current[2] - current[0]) > 2 * (candidate[2] - candidate[0]) or (
        current[3] - current[1]
    ) > 2 * (candidate[3] - candidate[1])
    return current if fits and not oversized else candidate


class WorldCanvas:
    """Top-down 2D view of the arena: cameras, reference markers and tracked tags.

    Drawing is incremental. The static layer (grid, axes, cameras, references) is
    rebuilt only when the viewport, the window size or the scene changes, and each
    tag keeps its own canvas items which are moved with ``coords``; wiping the
    canvas on every tick made the view flash.
    """

    BACKGROUND = "#0f1216"
    GRID = "#1d242b"
    AXIS_X = "#e2553d"
    AXIS_Y = "#5bbd6a"
    REFERENCE = "#b07cd6"
    CAMERA = "#f0a33c"
    TRAIL = "#3d4a56"
    TAG_FRESH = "#4ec9e0"
    TAG_STALE = "#7a848c"
    TEXT = "#c8d2da"
    GRID_STEPS_M = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
    MIN_GRID_PX = 55
    MARGIN_PX = 28
    STATIC = "static"

    def __init__(self, parent, model: WorldModel, tk_module) -> None:
        self.model = model
        self.canvas = tk_module.Canvas(
            parent, background=self.BACKGROUND, highlightthickness=0, width=640, height=360
        )
        self._scale_px_m = 1.0
        self._center = (0.0, 0.0)
        self._size = (0, 0)
        self._viewport: tuple[float, float, float, float] | None = None
        self._static_signature: tuple | None = None
        self._tag_items: dict[int, dict[str, int]] = {}
        self._summary_item: int | None = None

    @property
    def widget(self):
        return self.canvas

    def _to_pixels(self, x_m: float, y_m: float) -> tuple[float, float]:
        width, height = self._size
        center_x, center_y = self._center
        # World Y points "up" in the arena frame, canvas Y grows downwards.
        return (
            width / 2 + (x_m - center_x) * self._scale_px_m,
            height / 2 - (y_m - center_y) * self._scale_px_m,
        )

    def redraw(self, now_ns: int | None = None) -> None:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width < 2 * self.MARGIN_PX or height < 2 * self.MARGIN_PX:
            return
        self._size = (width, height)
        viewport = fit_viewport(self._viewport, self.model.bounds())
        self._viewport = viewport
        min_x, min_y, max_x, max_y = viewport
        self._scale_px_m = min(
            (width - 2 * self.MARGIN_PX) / max(max_x - min_x, 0.5),
            (height - 2 * self.MARGIN_PX) / max(max_y - min_y, 0.5),
        )
        self._center = ((min_x + max_x) / 2, (min_y + max_y) / 2)
        signature = (
            viewport,
            width,
            height,
            tuple(self.model.references),
            tuple(sorted(self.model.cameras.items())),
        )
        if signature != self._static_signature:
            self.canvas.delete(self.STATIC)
            self._draw_static(*viewport)
            self.canvas.tag_lower(self.STATIC)
            self._static_signature = signature
        tags = self.model.tags(now_ns)
        self._sync_tags(tags, now_ns)
        self._sync_summary(tags, now_ns)

    # ------------------------------------------------------------ static layer
    def _grid_step_m(self) -> float:
        for step in self.GRID_STEPS_M:
            if step * self._scale_px_m >= self.MIN_GRID_PX:
                return step
        return self.GRID_STEPS_M[-1]

    def _draw_static(self, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
        self._draw_grid(min_x, min_y, max_x, max_y)
        self._draw_axes()
        self._draw_references()
        self._draw_cameras()
        self._draw_scale_bar()

    def _draw_grid(self, min_x: float, min_y: float, max_x: float, max_y: float) -> None:
        step = self._grid_step_m()
        line_x = math.floor(min_x / step) * step
        while line_x <= max_x:
            x_px, _ = self._to_pixels(line_x, 0.0)
            self.canvas.create_line(
                x_px, 0, x_px, self._size[1], fill=self.GRID, tags=self.STATIC
            )
            line_x += step
        line_y = math.floor(min_y / step) * step
        while line_y <= max_y:
            _, y_px = self._to_pixels(0.0, line_y)
            self.canvas.create_line(
                0, y_px, self._size[0], y_px, fill=self.GRID, tags=self.STATIC
            )
            line_y += step

    def _draw_axes(self) -> None:
        origin = self._to_pixels(0.0, 0.0)
        for end, color, label in (
            ((0.5, 0.0), self.AXIS_X, "X"),
            ((0.0, 0.5), self.AXIS_Y, "Y"),
        ):
            tip = self._to_pixels(*end)
            self.canvas.create_line(
                *origin, *tip, fill=color, width=2, arrow="last", tags=self.STATIC
            )
            self.canvas.create_text(
                *tip, text=label, fill=color, anchor="sw", font=("TkDefaultFont", 8),
                tags=self.STATIC,
            )

    def _draw_references(self) -> None:
        for marker_id, x_m, y_m in self.model.references:
            x_px, y_px = self._to_pixels(x_m, y_m)
            self.canvas.create_polygon(
                x_px, y_px - 5, x_px + 5, y_px, x_px, y_px + 5, x_px - 5, y_px,
                outline=self.REFERENCE, fill="", width=1, tags=self.STATIC,
            )
            self.canvas.create_text(
                x_px + 8, y_px, text=f"R{marker_id}", fill=self.REFERENCE, anchor="w",
                font=("TkDefaultFont", 7), tags=self.STATIC,
            )

    def _draw_cameras(self) -> None:
        for camera_id, (x_m, y_m, heading) in sorted(self.model.cameras.items()):
            x_px, y_px = self._to_pixels(x_m, y_m)
            self.canvas.create_oval(
                x_px - 5, y_px - 5, x_px + 5, y_px + 5,
                outline=self.CAMERA, width=2, tags=self.STATIC,
            )
            tip = self._to_pixels(x_m + 0.4 * math.cos(heading), y_m + 0.4 * math.sin(heading))
            self.canvas.create_line(
                x_px, y_px, *tip, fill=self.CAMERA, width=1, arrow="last", tags=self.STATIC
            )
            self.canvas.create_text(
                x_px + 8, y_px - 8, text=camera_id, fill=self.CAMERA, anchor="w",
                font=("TkDefaultFont", 8), tags=self.STATIC,
            )

    def _draw_scale_bar(self) -> None:
        step = self._grid_step_m()
        bar_px = step * self._scale_px_m
        base_y = self._size[1] - 14
        self.canvas.create_line(
            12, base_y, 12 + bar_px, base_y, fill=self.TEXT, width=2, tags=self.STATIC
        )
        self.canvas.create_text(
            16 + bar_px, base_y, text=f"{step:g} m", fill=self.TEXT, anchor="w",
            font=("TkDefaultFont", 8), tags=self.STATIC,
        )

    # ----------------------------------------------------------- dynamic layer
    def _sync_tags(self, tags: Sequence[TrackedTag], now_ns: int) -> None:
        for tag in tags:
            items = self._tag_items.get(tag.tag_id) or self._create_tag_items()
            self._tag_items[tag.tag_id] = items
            stale = tag.stale(now_ns)
            color = self.TAG_STALE if stale else self.TAG_FRESH
            x_px, y_px = self._to_pixels(tag.x_m, tag.y_m)
            self.canvas.coords(items["body"], x_px - 7, y_px - 7, x_px + 7, y_px + 7)
            self.canvas.itemconfigure(
                items["body"], outline=color, fill="" if stale or tag.predicted else color
            )
            tip = self._to_pixels(
                tag.x_m + 0.3 * math.cos(tag.heading_rad),
                tag.y_m + 0.3 * math.sin(tag.heading_rad),
            )
            self.canvas.coords(items["heading"], x_px, y_px, *tip)
            self.canvas.itemconfigure(items["heading"], fill=color)
            suffix = " (ferma)" if stale else (" (predetta)" if tag.predicted else "")
            self.canvas.coords(items["label"], x_px + 10, y_px + 10)
            self.canvas.itemconfigure(
                items["label"],
                text=f"ID {tag.tag_id}  {tag.x_m:.2f}, {tag.y_m:.2f} m{suffix}",
                fill=color,
            )
            self._sync_trail(items["trail"], tag.tag_id, now_ns)
        for tag_id in set(self._tag_items) - {tag.tag_id for tag in tags}:
            for item in self._tag_items.pop(tag_id).values():
                self.canvas.delete(item)

    def _create_tag_items(self) -> dict[str, int]:
        return {
            "trail": self.canvas.create_line(0, 0, 0, 0, fill=self.TRAIL, width=1, state="hidden"),
            "body": self.canvas.create_oval(0, 0, 0, 0, width=2),
            "heading": self.canvas.create_line(0, 0, 0, 0, width=2, arrow="last"),
            "label": self.canvas.create_text(
                0, 0, anchor="w", font=("TkDefaultFont", 8), text=""
            ),
        }

    def _sync_trail(self, item: int, tag_id: int, now_ns: int) -> None:
        trail = self.model.trail(tag_id, now_ns)
        if len(trail) < 2:
            self.canvas.itemconfigure(item, state="hidden")
            return
        points: list[float] = []
        for x_m, y_m in trail:
            points.extend(self._to_pixels(x_m, y_m))
        self.canvas.coords(item, *points)
        self.canvas.itemconfigure(item, state="normal")

    def _sync_summary(self, tags: Sequence[TrackedTag], now_ns: int) -> None:
        live = sum(1 for tag in tags if not tag.stale(now_ns))
        text = (
            f"tag visibili: {live}/{len(tags)}"
            if tags
            else "nessuna posa ricevuta: il server pubblica su <base>/pose/<tag_id>"
        )
        if self._summary_item is None:
            self._summary_item = self.canvas.create_text(
                0, 14, fill=self.TEXT, anchor="e", font=("TkDefaultFont", 9)
            )
        self.canvas.coords(self._summary_item, self._size[0] - 12, 14)
        self.canvas.itemconfigure(self._summary_item, text=text)


class ServerGuiApp:
    """The Tk window: launcher on top, roster in the middle, server console at the bottom."""

    def __init__(self, options: ServerLaunchOptions, cameras: Sequence[str] = ()) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self.options = options
        self.process = ServerProcess()
        self.monitor: StatusMonitor | None = None
        self.status = DeploymentStatus(cameras)
        self.world = WorldModel()
        self.app_config: AppConfig | None = None
        self.calibration_store: CalibrationStore | None = None
        self.root = tk.Tk()
        self.root.title("VisionSystem · server di fusione (modalità distribuita)")
        self.root.geometry("1040x720")
        self.root.minsize(820, 560)
        self.host_var = tk.StringVar(value=options.mqtt_host)
        self.port_var = tk.StringVar(value=str(options.mqtt_port))
        self.config_var = tk.StringVar(value="" if options.config is None else str(options.config))
        self.calibrations_var = tk.StringVar(value=str(options.calibrations))
        self.cache_var = tk.StringVar(value=str(options.cache))
        self.debug_var = tk.BooleanVar(value=options.debug)
        self.no_mqtt_var = tk.BooleanVar(value=options.no_mqtt)
        self.verbose_var = tk.BooleanVar(value=options.verbose)
        self.server_state_var = tk.StringVar(value="server: fermo")
        self.broker_state_var = tk.StringVar(value="broker: non collegato")
        self.fusion_state_var = tk.StringVar(value="pose pubblicate: 0 · tag: —")
        self._build_layout()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.connect_monitor()
        self.root.after(REFRESH_MS, self._refresh)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        ttk = self._ttk
        tk = self._tk
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        root.rowconfigure(2, weight=2)

        launcher = ttk.LabelFrame(root, text="Avvio server", padding=8)
        launcher.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        for column in (1, 4):
            launcher.columnconfigure(column, weight=1)

        ttk.Label(launcher, text="MQTT host").grid(row=0, column=0, sticky="w")
        ttk.Entry(launcher, textvariable=self.host_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(launcher, text="porta").grid(row=0, column=2, sticky="w")
        ttk.Entry(launcher, textvariable=self.port_var, width=8).grid(row=0, column=3, sticky="w")
        ttk.Button(launcher, text="Riconnetti", command=self.connect_monitor).grid(
            row=0, column=4, sticky="e"
        )

        self._path_row(launcher, 1, "config", self.config_var, directory=False)
        self._path_row(launcher, 2, "calibrations", self.calibrations_var, directory=True)
        self._path_row(launcher, 3, "cache stato", self.cache_var, directory=False)

        flags = ttk.Frame(launcher)
        flags.grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Checkbutton(flags, text="--debug (vista world)", variable=self.debug_var).pack(
            side="left"
        )
        ttk.Checkbutton(flags, text="--no-mqtt", variable=self.no_mqtt_var).pack(
            side="left", padx=12
        )
        ttk.Checkbutton(flags, text="--verbose", variable=self.verbose_var).pack(side="left")

        actions = ttk.Frame(launcher)
        actions.grid(row=5, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(actions, text="Avvia server", command=self.start_server)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(
            actions, text="Ferma server", command=self.stop_server, state="disabled"
        )
        self.stop_button.pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.server_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.broker_state_var).pack(side="left", padx=12)
        ttk.Label(actions, textvariable=self.fusion_state_var).pack(side="right")

        table_frame = ttk.LabelFrame(root, text="Camere del deployment", padding=8)
        table_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("camera", "nodo", "pubblicate", "server", "ricevute", "age", "calib", "note")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        headings = {
            "camera": ("Camera", 90),
            "nodo": ("Nodo", 60),
            "pubblicate": ("Oss. pubblicate", 120),
            "server": ("Server", 70),
            "ricevute": ("Oss. ricevute", 110),
            "age": ("Età (ms)", 90),
            "calib": ("Calibrata", 90),
            "note": ("Note", 320),
        }
        for column, (title, width) in headings.items():
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w", stretch=column == "note")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))

        world_frame = ttk.Frame(notebook, padding=6)
        world_frame.columnconfigure(0, weight=1)
        world_frame.rowconfigure(0, weight=1)
        self.world_canvas = WorldCanvas(world_frame, self.world, tk)
        self.world_canvas.widget.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            world_frame,
            text=(
                "Pose fuse pubblicate dal server: ● tag visibile, ○ posa predetta o ferma, "
                "arancio = camere calibrate, viola = reference marker."
            ),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        notebook.add(world_frame, text="Vista world (robot tracciati)")

        log_frame = ttk.Frame(notebook, padding=6)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, wrap="none", height=12, state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.grid(row=0, column=1, sticky="ns")
        ttk.Button(log_frame, text="Pulisci", command=self.clear_log).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        notebook.add(log_frame, text="Console ed eventi")

    def _path_row(self, parent, row: int, label: str, variable, directory: bool) -> None:
        ttk = self._ttk
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row, column=1, columnspan=3, sticky="ew", padx=4, pady=2
        )
        ttk.Button(
            parent, text="Sfoglia…", command=lambda: self._browse(variable, directory)
        ).grid(row=row, column=4, sticky="e", pady=2)

    def _browse(self, variable, directory: bool) -> None:
        from tkinter import filedialog

        current = variable.get()
        initial = str(Path(current).parent if current and not directory else current or ".")
        chosen = (
            filedialog.askdirectory(initialdir=initial)
            if directory
            else filedialog.askopenfilename(
                initialdir=initial, filetypes=[("JSON", "*.json"), ("Tutti i file", "*.*")]
            )
        )
        if chosen:
            variable.set(chosen)

    # ----------------------------------------------------------------- actions
    def current_options(self) -> ServerLaunchOptions:
        config = self.config_var.get().strip()
        try:
            port = int(self.port_var.get())
        except ValueError:
            port = self.options.mqtt_port
            self.port_var.set(str(port))
            self.append_log("[gui] porta MQTT non valida: uso quella precedente")
        return ServerLaunchOptions(
            config=Path(config) if config else None,
            calibrations=Path(self.calibrations_var.get().strip() or DEFAULT_CALIBRATIONS),
            cache=Path(self.cache_var.get().strip() or DEFAULT_CACHE),
            mqtt_host=self.host_var.get().strip() or "localhost",
            mqtt_port=port,
            debug=self.debug_var.get(),
            no_mqtt=self.no_mqtt_var.get(),
            verbose=self.verbose_var.get(),
        )

    def connect_monitor(self) -> None:
        """(Re)subscribe to the deployment topics implied by the current config."""
        options = self.current_options()
        self.options = options
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None
        try:
            config, cameras = roster_from_config(options.config, options.cache)
        except (OSError, ValueError) as error:
            self.append_log(f"[gui] configurazione non leggibile: {error}")
            return
        self.status = DeploymentStatus(cameras)
        self.app_config = config
        self.world.reset()
        self.calibration_store = CalibrationStore(options.calibrations)
        self.world.update_scene(config, self.calibration_store.calibrations)
        settings = MqttSettings.from_environment()
        settings = MqttSettings(
            host=options.mqtt_host,
            port=options.mqtt_port,
            username=settings.username,
            password=settings.password,
            tls=settings.tls,
        )
        self.monitor = StatusMonitor(config.base_topic, settings)
        self.monitor.start()
        self.append_log(
            f"[gui] in ascolto su {options.mqtt_host}:{options.mqtt_port} "
            f"topic {config.base_topic}/# (camere: {', '.join(cameras) or 'nessuna'})"
        )

    def start_server(self) -> None:
        if self.process.running:
            return
        options = self.current_options()
        self.options = options
        if self.status.coordinator_online():
            self.append_log(
                "[gui] attenzione: un coordinatore sta già pubblicando metriche su questo "
                "broker; due server pubblicano le stesse pose"
            )
        try:
            self.process.start(options)
        except (OSError, RuntimeError) as error:
            self.append_log(f"[gui] avvio fallito: {error}")
            return
        # Reconnecting resets the roster view, so only do it when the launcher fields
        # no longer match the broker the panel is listening to.
        if self.monitor is None or (self.monitor.settings.host, self.monitor.settings.port) != (
            options.mqtt_host,
            options.mqtt_port,
        ):
            self.connect_monitor()

    def stop_server(self) -> None:
        if not self.process.running:
            return
        self.append_log("[gui] arresto del server in corso…")
        self.process.stop()

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def append_log(self, line: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n")
        excess = int(self.log.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    # ----------------------------------------------------------------- refresh
    def _refresh(self) -> None:
        for line in self.process.drain_logs():
            self.append_log(line)
        if self.monitor is not None:
            for topic, body in self.monitor.drain():
                if "/pose/" in topic:
                    self.world.apply_pose(body)
                    continue
                line = self.status.apply(topic, body)
                if line:
                    self.append_log(line)
        # Extrinsics change while calibrating a node: the store throttles the rescan
        # itself, so polling every tick costs nothing.
        if self.calibration_store is not None and self.calibration_store.reload_if_changed():
            self.world.update_scene(self.app_config, self.calibration_store.calibrations)
            self.append_log("[gui] calibrazioni ricaricate dal disco")
        self._refresh_indicators()
        self._refresh_table()
        self.world_canvas.redraw()
        self.root.after(REFRESH_MS, self._refresh)

    @staticmethod
    def _set_if_changed(variable, value: str) -> None:
        if variable.get() != value:
            variable.set(value)

    def _refresh_indicators(self) -> None:
        # Widgets are only touched when their value actually changes: reassigning
        # the same text four times a second repaints them for nothing.
        running = self.process.running
        for button, state in (
            (self.start_button, "disabled" if running else "normal"),
            (self.stop_button, "normal" if running else "disabled"),
        ):
            if str(button.cget("state")) != state:
                button.configure(state=state)
        if running:
            server_state = f"server: in esecuzione (pid {self.process.pid})"
        elif self.process.exit_code is None:
            server_state = "server: fermo"
        else:
            server_state = f"server: uscito (codice {self.process.exit_code})"
        self._set_if_changed(self.server_state_var, server_state)
        if self.monitor is None:
            broker_state = "broker: non collegato"
        elif self.monitor.connected.is_set():
            fusion = "coordinatore attivo" if self.status.coordinator_online() else "in attesa"
            broker_state = (
                f"broker: {self.monitor.settings.host}:{self.monitor.settings.port} · {fusion}"
            )
        else:
            broker_state = f"broker: {self.monitor.error or 'connessione in corso…'}"
        self._set_if_changed(self.broker_state_var, broker_state)
        tags = ", ".join(str(tag) for tag in self.status.tracked_tags) or "—"
        self._set_if_changed(
            self.fusion_state_var, f"pose pubblicate: {self.status.poses_published} · tag: {tags}"
        )

    def _refresh_table(self) -> None:
        rows = self.status.rows()
        existing = set(self.table.get_children())
        for row in rows:
            values = (
                row.camera_id,
                _format_flag(row.node_online),
                _format_optional(row.node_observations),
                _format_flag(row.server_online),
                _format_optional(row.observations_received),
                _format_optional(row.age_ms),
                "—" if row.calibrated is None else ("sì" if row.calibrated else "no"),
                row.note,
            )
            if row.camera_id in existing:
                if tuple(self.table.item(row.camera_id, "values")) != tuple(
                    str(value) for value in values
                ):
                    self.table.item(row.camera_id, values=values)
                existing.discard(row.camera_id)
            else:
                self.table.insert("", "end", iid=row.camera_id, values=values)
        for stale in existing:
            self.table.delete(stale)

    def shutdown(self) -> None:
        """Stop the child server and the MQTT listener; safe to call more than once.

        The server runs in its own session so that a Ctrl-C in the launching
        terminal cannot kill it behind the panel's back; the flip side is that the
        panel must always stop it itself, including when it exits via a signal.
        """
        if self.process.running:
            self.process.stop()
        if self.monitor is not None:
            self.monitor.stop()
            self.monitor = None

    def request_stop(self) -> None:
        """Leave the main loop from a signal handler; cleanup happens in `run`."""
        self.root.quit()

    def on_close(self) -> None:
        self.shutdown()
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()


def server_gui_main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Pannello grafico per avviare il server di fusione e sorvegliare "
            "nodi e camere nel deployment distribuito"
        )
    )
    parser.add_argument("--config", type=Path, help="config iniziale mostrata nel pannello")
    parser.add_argument("--calibrations", type=Path, default=DEFAULT_CALIBRATIONS)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--mqtt-host", default=os.getenv("VISION_MQTT_HOST", "localhost"), help="broker MQTT"
    )
    parser.add_argument(
        "--mqtt-port", type=int, default=int(os.getenv("VISION_MQTT_PORT", "1883"))
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--no-debug-window",
        action="store_true",
        help="non pre-selezionare --debug (vista world) per il server",
    )
    args = parser.parse_args()
    diagnostic_path = configure_diagnostics("vision-server-gui", verbose=args.verbose)
    print(f"Log diagnostico: {diagnostic_path}")
    options = ServerLaunchOptions(
        config=args.config,
        calibrations=args.calibrations,
        cache=args.cache,
        mqtt_host=args.mqtt_host,
        mqtt_port=args.mqtt_port,
        debug=not args.no_debug_window,
        verbose=args.verbose,
    )
    cameras: Iterable[str] = ()
    try:
        _, cameras = roster_from_config(options.config, options.cache)
    except (OSError, ValueError) as error:
        print(f"Configurazione non leggibile ({error}): il roster verrà scoperto da MQTT")
    try:
        import tkinter
    except ImportError:
        parser.error(
            "tkinter non è disponibile: installare il pacchetto python3-tk "
            "(Debian/Ubuntu: sudo apt install python3-tk)"
        )
    try:
        app = ServerGuiApp(options, list(cameras))
    except tkinter.TclError as error:
        parser.error(f"impossibile aprire la finestra grafica (DISPLAY assente?): {error}")
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signal_number, lambda signum, frame: app.request_stop())
    app.run()


if __name__ == "__main__":
    server_gui_main()
