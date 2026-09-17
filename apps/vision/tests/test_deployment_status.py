"""Merging node and coordinator metrics into the roster table."""

from vision_system.apps.server_gui.presenters import describe_message
from vision_system.monitoring.deployment import (
    PRESENCE_TIMEOUT_NS,
    CameraIssue,
    DeploymentStatus,
)
from vision_system.transport.payloads import parse


def _apply(status, topic, body, now_ns):
    """Decode like the panel does, feed the model, return the console line if any."""
    message = parse(topic, body)
    if message is None:
        return None
    status.apply(message, now_ns)
    return describe_message(message)


def test_status_merges_node_and_coordinator_views():
    status = DeploymentStatus(["cam_0", "cam_1"])
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 120},
        now_ns=now,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {
            "role": "coordinator",
            "poses_published": 42,
            "tracked_tags": [7],
            "cameras": {
                "cam_0": {
                    "online": True,
                    "observations_received": 118,
                    "calibrated": True,
                    "age_ms": 12.5,
                },
                "cam_1": {
                    "online": False,
                    "observations_received": 0,
                    "calibrated": False,
                    "age_ms": None,
                },
            },
        },
        now_ns=now,
    )
    rows = {row.camera_id: row for row in status.rows(now_ns=now)}
    assert status.poses_published == 42
    assert status.tracked_tags == [7]
    assert rows["cam_0"].node_online and rows["cam_0"].server_online
    assert rows["cam_0"].node_observations == 120
    assert rows["cam_0"].observations_received == 118
    assert rows["cam_0"].issues == ()
    assert rows["cam_1"].server_online is False
    assert CameraIssue.CALIBRATION_MISSING_ON_SERVER in rows["cam_1"].issues


def test_status_flags_node_publishing_without_reaching_the_server():
    status = DeploymentStatus(["cam_0"])
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 5},
        now_ns=now,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "coordinator", "poses_published": 0, "cameras": {"cam_0": {"online": False}}},
        now_ns=now,
    )
    row = status.rows(now_ns=now)[0]
    assert row.node_online is True
    assert row.server_online is False
    assert CameraIssue.NODE_UP_NO_OBSERVATIONS in row.issues


def test_status_expires_silent_processes():
    status = DeploymentStatus(["cam_0"])
    start = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 5},
        now_ns=start,
    )
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {"role": "coordinator", "cameras": {"cam_0": {"online": True}}},
        now_ns=start,
    )
    later = start + PRESENCE_TIMEOUT_NS + 1
    row = status.rows(now_ns=later)[0]
    assert row.node_online is False
    assert row.server_online is False
    assert status.coordinator_online(now_ns=later) is False


def test_status_discovers_unexpected_cameras_and_reports_drift():
    status = DeploymentStatus()
    now = 10 * PRESENCE_TIMEOUT_NS
    _apply(
        status,
        "vision/default/indoor-01/metrics",
        {
            "role": "node",
            "camera_id": "cam_3",
            "observations_published": 0,
            "excluded_for_drift": True,
        },
        now_ns=now,
    )
    row = status.rows(now_ns=now)[0]
    assert row.camera_id == "cam_3"
    assert CameraIssue.DRIFT_RECALIBRATE in row.issues


def test_status_formats_events_and_status_messages():
    status = DeploymentStatus()
    now = 10 * PRESENCE_TIMEOUT_NS
    line = _apply(
        status,
        "vision/default/indoor-01/event",
        {
            "code": "MISSING_CALIBRATION",
            "message": "Cameras without calibration",
            "severity": "warning",
            "cameras": ["cam_2"],
        },
        now_ns=now,
    )
    assert line is not None and line.startswith("[WARNING] MISSING_CALIBRATION")
    assert "cam_2" in line
    assert _apply(
        status,
        "vision/default/indoor-01/status", {"online": True, "reason": "running"}, now_ns=now
    )
    assert _apply(status, "vision/default/indoor-01/pose/7", {"tag_id": 7}, now_ns=now) is None


