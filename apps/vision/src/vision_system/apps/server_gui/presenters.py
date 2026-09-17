"""Turning deployment models into the text the panel shows. Pure functions only."""

from __future__ import annotations

from collections.abc import Sequence

from ...monitoring.deployment import CameraIssue
from ...transport.payloads import DeploymentEvent, DeploymentMessage, ServerStatus
from . import strings


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
