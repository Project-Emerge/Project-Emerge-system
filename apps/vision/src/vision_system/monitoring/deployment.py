"""Roster presence model: what each node claims to publish and what the server receives."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..transport.payloads import (
    CoordinatorMetrics,
    DeploymentEvent,
    DeploymentMessage,
    NodeMetrics,
    ServerStatus,
)

# Nodes and coordinator publish metrics once per second: three missed reports is a
# generous "this process is gone" threshold that survives a hiccup on the broker.
PRESENCE_TIMEOUT_NS = 3_000_000_000


class CameraIssue(StrEnum):
    """Why a roster line deserves the operator's attention.

    These are codes, not sentences: the model stays language-neutral and the panel
    decides how to word them.
    """

    DRIFT_RECALIBRATE = "drift_recalibrate"
    CALIBRATION_MISSING_ON_SERVER = "calibration_missing_on_server"
    NODE_UP_NO_OBSERVATIONS = "node_up_no_observations"
    OBSERVATIONS_WITHOUT_NODE_METRICS = "observations_without_node_metrics"
    NODE_ABSENT = "node_absent"
    CAMERA_NOT_CAPTURING = "camera_not_capturing"
    CAMERA_NO_FRAMES = "camera_no_frames"
    CALIBRATION_MISSING_ON_NODE = "calibration_missing_on_node"


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
    issues: tuple[CameraIssue, ...] = ()
    # What the node says about the webcam itself. ``None`` throughout means no
    # node has reported, or an older one that does not publish capture state.
    capture_online: bool | None = None
    capture_error: str | None = None
    frames_received: int | None = None
    source: int | str | None = None


class DeploymentStatus:
    """Aggregates coordinator and node metrics into the roster table."""

    def __init__(self, camera_ids: Sequence[str] = ()) -> None:
        self.expected_camera_ids = list(camera_ids)
        self.coordinator: CoordinatorMetrics | None = None
        self.coordinator_seen_ns: int | None = None
        self.nodes: dict[str, NodeMetrics] = {}
        self.node_seen_ns: dict[str, int] = {}
        self.server_status: ServerStatus | None = None
        self.poses_published = 0
        self.tracked_tags: list[int] = []

    def apply(self, message: DeploymentMessage, now_ns: int) -> None:
        """Consume one decoded message. Dispatch already happened in payloads.parse."""
        match message:
            case CoordinatorMetrics():
                self.coordinator = message
                self.coordinator_seen_ns = now_ns
                self.poses_published = message.poses_published
                self.tracked_tags = list(message.tracked_tags)
                for camera_id in message.cameras:
                    self._register(camera_id)
            case NodeMetrics():
                self._register(message.camera_id)
                self.nodes[message.camera_id] = message
                self.node_seen_ns[message.camera_id] = now_ns
            case ServerStatus():
                self.server_status = message
            case DeploymentEvent():
                pass

    def _register(self, camera_id: str) -> None:
        if camera_id not in self.expected_camera_ids:
            self.expected_camera_ids.append(camera_id)

    def coordinator_online(self, now_ns: int) -> bool:
        return (
            self.coordinator_seen_ns is not None
            and now_ns - self.coordinator_seen_ns < PRESENCE_TIMEOUT_NS
        )

    def rows(self, now_ns: int) -> list[CameraRow]:
        cameras = self.coordinator.cameras if self.coordinator is not None else {}
        rows: list[CameraRow] = []
        for camera_id in self.expected_camera_ids:
            server_view = cameras.get(camera_id)
            node_view = self.nodes.get(camera_id)
            seen_ns = self.node_seen_ns.get(camera_id)
            node_online = seen_ns is not None and now_ns - seen_ns < PRESENCE_TIMEOUT_NS
            server_online = (
                server_view is not None
                and server_view.online
                and self.coordinator_online(now_ns)
            )
            calibrated = server_view.calibrated if server_view is not None else None
            issues = self._issues(
                node_view, node_online=node_online, server_online=server_online,
                calibrated=calibrated,
            )
            rows.append(
                CameraRow(
                    camera_id=camera_id,
                    node_online=node_online,
                    node_observations=(
                        node_view.observations_published if node_view is not None else None
                    ),
                    server_online=server_online,
                    observations_received=(
                        server_view.observations_received if server_view is not None else None
                    ),
                    age_ms=server_view.age_ms if server_view is not None else None,
                    calibrated=calibrated,
                    issues=issues,
                    capture_online=node_view.capture_online if node_view else None,
                    capture_error=node_view.capture_error if node_view else None,
                    frames_received=node_view.frames_received if node_view else None,
                    source=node_view.source if node_view else None,
                )
            )
        return rows

    @staticmethod
    def _issues(
        node: NodeMetrics | None,
        *,
        node_online: bool,
        server_online: bool,
        calibrated: bool | None,
    ) -> tuple[CameraIssue, ...]:
        """Name every distinct reason this camera is not contributing poses.

        The stages are reported separately rather than collapsed into one
        "offline": a node that never started, a webcam that will not open, a
        camera nobody calibrated and a node whose observations are not arriving
        are four different problems with four different fixes, and the panel used
        to show the same line for all of them.
        """
        issues: list[CameraIssue] = []
        if node is not None and node.excluded_for_drift:
            issues.append(CameraIssue.DRIFT_RECALIBRATE)
        if calibrated is False:
            issues.append(CameraIssue.CALIBRATION_MISSING_ON_SERVER)
        if node_online and node is not None:
            if node.capture_online is False:
                issues.append(CameraIssue.CAMERA_NOT_CAPTURING)
            elif node.capture_online and not node.frames_received:
                issues.append(CameraIssue.CAMERA_NO_FRAMES)
            if node.calibrated is False:
                issues.append(CameraIssue.CALIBRATION_MISSING_ON_NODE)
        if not node_online and not server_online:
            issues.append(CameraIssue.NODE_ABSENT)
        # Only worth saying when the capture is healthy: otherwise the missing
        # observations are already explained by the camera issue above.
        if node_online and not server_online and not issues:
            issues.append(CameraIssue.NODE_UP_NO_OBSERVATIONS)
        if not node_online and server_online:
            issues.append(CameraIssue.OBSERVATIONS_WITHOUT_NODE_METRICS)
        return tuple(issues)
