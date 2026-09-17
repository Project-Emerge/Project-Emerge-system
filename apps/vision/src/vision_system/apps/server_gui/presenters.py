"""Turning deployment models into the text the panel shows. Pure functions only."""

from __future__ import annotations

from collections.abc import Sequence

from ...monitoring.deployment import CameraIssue
from ...transport.payloads import DeploymentEvent, DeploymentMessage, ServerStatus

ISSUE_TEXT: dict[CameraIssue, str] = {
    CameraIssue.DRIFT_RECALIBRATE: "drift: ricalibrare",
    CameraIssue.CALIBRATION_MISSING_ON_SERVER: "calibrazione assente sul server",
    CameraIssue.NODE_UP_NO_OBSERVATIONS: "nodo attivo ma nessuna osservazione al server",
    CameraIssue.OBSERVATIONS_WITHOUT_NODE_METRICS: "osservazioni senza metriche del nodo",
}


def describe_issues(issues: Sequence[CameraIssue]) -> str:
    """Render a roster line's issue codes as one cell of text."""
    return "; ".join(ISSUE_TEXT[issue] for issue in issues)


def describe_message(message: DeploymentMessage) -> str | None:
    """The console line a message deserves, or None when it is not worth logging."""
    if isinstance(message, DeploymentEvent):
        return f"[{message.severity}] {message.code}: {message.message}{message.context_suffix()}"
    if isinstance(message, ServerStatus):
        return f"[status] online={message.online} {message.reason}".strip()
    return None
