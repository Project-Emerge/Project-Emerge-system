"""Turning deployment models into the text the panel shows. Pure functions only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ...monitoring.deployment import CameraIssue, CameraRow
from ...transport.payloads import DeploymentEvent, DeploymentMessage, ServerStatus
from . import strings
from .models import Notice, NoticeCode, PanelSnapshot

NOTICE_TEXT: dict[NoticeCode, str] = {
    NoticeCode.INVALID_PORT: strings.INVALID_PORT,
    NoticeCode.CONFIG_UNREADABLE: strings.CONFIG_UNREADABLE,
    NoticeCode.DEFAULT_ROSTER: strings.DEFAULT_ROSTER,
    NoticeCode.LISTENING: strings.LISTENING,
    NoticeCode.START_FAILED: strings.START_FAILED,
    NoticeCode.STOPPING: strings.STOPPING,
    NoticeCode.SECOND_COORDINATOR: strings.SECOND_COORDINATOR,
    NoticeCode.CALIBRATIONS_RELOADED: strings.CALIBRATIONS_RELOADED,
}


def describe_issues(issues: Sequence[CameraIssue]) -> str:
    """Render a roster line's issue codes as one cell of text."""
    return "; ".join(strings.ISSUE_TEXT[issue] for issue in issues)


def describe_message(message: DeploymentMessage) -> str | None:
    """The console line a message deserves, or None when it is not worth logging."""
    if isinstance(message, DeploymentEvent):
        return f"[{message.severity}] {message.code}: {message.message}{message.context_suffix()}"
    if isinstance(message, ServerStatus):
        return f"[status] online={message.online} {message.reason}".strip()
    return None


@dataclass(frozen=True)
class IndicatorTexts:
    """The three status lines under the launcher buttons."""

    server: str
    broker: str
    fusion: str


def format_flag(value: bool) -> str:
    return "●" if value else "○"


def format_optional(value: object) -> str:
    return strings.UNKNOWN if value is None else str(value)


def format_calibrated(value: bool | None) -> str:
    if value is None:
        return strings.UNKNOWN
    return strings.YES if value else strings.NO


def indicator_texts(snapshot: PanelSnapshot) -> IndicatorTexts:
    """Render the server, broker and fusion indicators for one frame."""
    if snapshot.process_running:
        server = strings.SERVER_RUNNING.format(pid=snapshot.pid)
    elif snapshot.exit_code is None:
        server = strings.SERVER_STOPPED
    else:
        server = strings.SERVER_EXITED.format(code=snapshot.exit_code)

    if snapshot.broker is None:
        broker = strings.BROKER_DISCONNECTED
    elif snapshot.broker_connected:
        host, port = snapshot.broker
        fusion_state = (
            strings.FUSION_ACTIVE if snapshot.coordinator_online else strings.FUSION_WAITING
        )
        broker = strings.BROKER_CONNECTED.format(host=host, port=port) + f" · {fusion_state}"
    elif snapshot.broker_error:
        broker = strings.BROKER_ERROR.format(error=snapshot.broker_error)
    else:
        broker = strings.BROKER_CONNECTING

    tags = ", ".join(str(tag) for tag in snapshot.tracked_tags) or strings.UNKNOWN
    fusion = strings.FUSION_SUMMARY.format(poses=snapshot.poses_published, tags=tags)
    return IndicatorTexts(server=server, broker=broker, fusion=fusion)


def roster_values(row: CameraRow) -> tuple[str, ...]:
    """One roster line, in the column order of strings.ROSTER_COLUMNS."""
    return (
        row.camera_id,
        format_flag(row.node_online),
        format_optional(row.node_observations),
        format_flag(row.server_online),
        format_optional(row.observations_received),
        format_optional(row.age_ms),
        format_calibrated(row.calibrated),
        describe_issues(row.issues),
    )


def format_notice(notice: Notice) -> str:
    """Word a notice code. Every code must have an entry, so a miss raises KeyError."""
    template = NOTICE_TEXT[notice.code]
    fields = dict(notice.fields)
    if "cameras" in fields and isinstance(fields["cameras"], list):
        fields["cameras"] = ", ".join(str(item) for item in fields["cameras"]) or strings.NO_CAMERAS
    return template.format(**fields)
