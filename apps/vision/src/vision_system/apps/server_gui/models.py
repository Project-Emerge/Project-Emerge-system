"""Value objects exchanged between the controller and the view.

They live apart from both so that presenters can word a Notice without importing
the controller, and the controller can raise one without importing presenters.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from ...monitoring.deployment import CameraRow


class NoticeCode(StrEnum):
    """Something the operator should be told, named rather than worded."""

    INVALID_PORT = "invalid_port"
    CONFIG_UNREADABLE = "config_unreadable"
    DEFAULT_ROSTER = "default_roster"
    LISTENING = "listening"
    START_FAILED = "start_failed"
    STOPPING = "stopping"
    SECOND_COORDINATOR = "second_coordinator"
    CALIBRATIONS_RELOADED = "calibrations_reloaded"


@dataclass(frozen=True)
class Notice:
    """A notice code plus whatever the wording needs to interpolate."""

    code: NoticeCode
    fields: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class LauncherFields:
    """The launcher form exactly as typed, before any validation."""

    config: str = ""
    calibrations: str = ""
    cache: str = ""
    host: str = ""
    port: str = ""
    debug: bool = True
    no_mqtt: bool = False
    verbose: bool = False


@dataclass(frozen=True)
class PanelSnapshot:
    """Everything the widgets need for one frame. Pure data."""

    now_ns: int
    process_running: bool
    pid: int | None
    exit_code: int | None
    broker: tuple[str, int] | None
    broker_connected: bool
    broker_error: str | None
    coordinator_online: bool
    poses_published: int
    tracked_tags: tuple[int, ...]
    rows: tuple[CameraRow, ...]


