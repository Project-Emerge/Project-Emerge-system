"""Every user-facing string of the server panel, in one place and in English.

Keeping them here is what lets the models under ``monitoring/`` stay language
neutral: they emit codes, and this module decides the wording.
"""

from __future__ import annotations

from typing import Final

from ...monitoring.deployment import CameraIssue

# --------------------------------------------------------------------- window
WINDOW_TITLE: Final = "VisionSystem · fusion server (distributed mode)"

# ------------------------------------------------------------------- launcher
LAUNCHER_FRAME: Final = "Server launch"
MQTT_HOST_LABEL: Final = "MQTT host"
MQTT_PORT_LABEL: Final = "port"
RECONNECT_BUTTON: Final = "Reconnect"
CONFIG_LABEL: Final = "config"
CALIBRATIONS_LABEL: Final = "calibrations"
CACHE_LABEL: Final = "state cache"
BROWSE_BUTTON: Final = "Browse…"
ALL_FILES: Final = "All files"
START_BUTTON: Final = "Start server"
STOP_BUTTON: Final = "Stop server"

# ---------------------------------------------------------------- indicators
SERVER_STOPPED: Final = "server: stopped"
SERVER_RUNNING: Final = "server: running (pid {pid})"
SERVER_EXITED: Final = "server: exited with code {code}"
BROKER_DISCONNECTED: Final = "broker: not connected"
BROKER_CONNECTED: Final = "broker: {host}:{port}"
BROKER_CONNECTING: Final = "broker: connecting…"
BROKER_ERROR: Final = "broker: {error}"
FUSION_SUMMARY: Final = "poses published: {poses} · tags: {tags}"
FUSION_WAITING: Final = "waiting"
FUSION_ACTIVE: Final = "coordinator active"

# --------------------------------------------------------------------- roster
ROSTER_FRAME: Final = "Deployment cameras"
# The columns follow the pipeline in order — node process, webcam, frames, then
# what the server actually received — so a gap in the chain is read off the row
# left to right instead of guessed from a single "offline" flag.
ROSTER_COLUMNS: Final = (
    ("camera", "Camera", 80),
    ("node", "Node", 55),
    ("capture", "Camera up", 80),
    ("frames", "Frames", 75),
    ("published", "Obs. published", 110),
    ("server", "Server", 65),
    ("received", "Obs. received", 105),
    ("age", "Age (ms)", 80),
    ("calibrated", "Calibrated", 80),
    ("issues", "Notes", 340),
)
YES: Final = "yes"
NO: Final = "no"
UNKNOWN: Final = "—"

ISSUE_TEXT: Final[dict[CameraIssue, str]] = {
    CameraIssue.DRIFT_RECALIBRATE: "drift: recalibrate",
    CameraIssue.CALIBRATION_MISSING_ON_SERVER: "calibration missing on the server",
    CameraIssue.NODE_UP_NO_OBSERVATIONS: "node up but no observation reaches the server",
    CameraIssue.OBSERVATIONS_WITHOUT_NODE_METRICS: "observations without node metrics",
    CameraIssue.NODE_ABSENT: "no vision-node is publishing for this camera",
    CameraIssue.CAMERA_NOT_CAPTURING: "the webcam does not open",
    CameraIssue.CAMERA_NO_FRAMES: "the webcam is open but delivers no frame",
    CameraIssue.CALIBRATION_MISSING_ON_NODE: "calibration missing on the node PC",
}
CAPTURE_ERROR: Final = "{issues} ({error})"
CAPTURE_SOURCE: Final = "source {source}"

# ------------------------------------------------------------------ notebook
WORLD_TAB: Final = "World view (tracked robots)"
WORLD_CAPTION: Final = (
    "Fused poses published by the server: ● tag visible, ○ predicted or still pose, "
    "orange = calibrated cameras, purple = reference markers."
)
CONSOLE_TAB: Final = "Console and events"
CLEAR_BUTTON: Final = "Clear"

# ---------------------------------------------------------- console messages
GUI_PREFIX: Final = "[gui]"
INVALID_PORT: Final = "[gui] invalid MQTT port: keeping the previous one"
LISTENING: Final = "[gui] listening on topic {topic}/# (cameras: {cameras})"
NO_CAMERAS: Final = "none"
SECOND_COORDINATOR: Final = (
    "[gui] warning: a coordinator is already publishing metrics on this broker; "
    "starting a second one would publish duplicate poses"
)
CALIBRATIONS_RELOADED: Final = "[gui] calibrations reloaded from disk"
STOPPING: Final = "[gui] stopping the server…"

# ------------------------------------------------------- supervisor messages
ALREADY_RUNNING: Final = "the server is already running"
SERVER_EXIT_LINE: Final = "[server exited with code {code}]"

# --------------------------------------------------------------- cli messages
CLI_DESCRIPTION: Final = (
    "Graphical panel to launch the fusion server and watch nodes and cameras "
    "in a distributed deployment"
)
CLI_CONFIG_HELP: Final = "configuration shown in the panel at startup"
CLI_MQTT_HOST_HELP: Final = "MQTT broker"
CLI_NO_DEBUG_HELP: Final = "do not pre-select --debug (world view) for the server"
CLI_NO_MQTT_HELP: Final = "pre-select --no-mqtt for the server"
DIAGNOSTIC_LOG: Final = "Diagnostic log: {path}"
CONFIG_UNREADABLE: Final = (
    "Configuration unreadable ({error}): the roster will be discovered over MQTT"
)
TKINTER_MISSING: Final = (
    "tkinter is not available: install python3-tk "
    "(Debian/Ubuntu: sudo apt install python3-tk)"
)
DISPLAY_UNAVAILABLE: Final = "cannot open the GUI window (is DISPLAY set?): {error}"

# ---------------------------------------------------------- transport errors
MQTT_REFUSED: Final = "mqtt connection refused: {reason_code}"
START_FAILED: Final = "[gui] could not start the server: {error}"
DEFAULT_ROSTER: Final = (
    "[gui] no config and no state cache: falling back to the built-in roster ({cameras})"
)
