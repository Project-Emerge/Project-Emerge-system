"""Roster presence model: what each node claims to publish and what the server receives."""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

# Nodes and coordinator publish metrics once per second: three missed reports is a
# generous "this process is gone" threshold that survives a hiccup on the broker.
PRESENCE_TIMEOUT_NS = 3_000_000_000


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
