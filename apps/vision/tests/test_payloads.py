import math

import pytest

from vision_system.transport.payloads import (
    CoordinatorMetrics,
    DeploymentEvent,
    MessageKind,
    NodeMetrics,
    PoseUpdate,
    ServerStatus,
    classify,
    parse,
)


def _pose_body(**overrides):
    body = {"tag_id": 7, "position_m": {"x": 1.0, "y": 2.0, "z": 0.5}}
    body.update(overrides)
    return body


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("vision/lab/indoor-01/metrics", MessageKind.METRICS),
        ("vision/lab/indoor-01/event", MessageKind.EVENT),
        ("vision/lab/indoor-01/status", MessageKind.STATUS),
        ("vision/lab/indoor-01/pose/23", MessageKind.POSE),
        ("vision/lab/indoor-01/config/set", MessageKind.UNKNOWN),
    ],
)
def test_classify_maps_every_subscribed_topic(topic, expected):
    assert classify(topic) is expected


def test_pose_topic_wins_over_the_suffix_rules():
    # A tag id could end in any word; the nested pose form must be recognised first.
    assert classify("vision/lab/indoor-01/pose/status") is MessageKind.POSE


def test_parse_selects_the_role_for_metrics():
    coordinator = parse("x/metrics", {"role": "coordinator", "poses_published": 3})
    node = parse("x/metrics", {"role": "node", "camera_id": "cam_1"})
    assert isinstance(coordinator, CoordinatorMetrics)
    assert coordinator.poses_published == 3
    assert isinstance(node, NodeMetrics)
    assert node.camera_id == "cam_1"


def test_parse_rejects_metrics_without_a_usable_role():
    assert parse("x/metrics", {"role": "node"}) is None          # no camera_id
    assert parse("x/metrics", {"role": "impostor"}) is None
    assert parse("x/unknown", {}) is None


def test_coordinator_metrics_tolerate_a_partial_camera_report():
    metrics = CoordinatorMetrics.from_body(
        {"role": "coordinator", "cameras": {"cam_0": {"online": True}}}
    )
    camera = metrics.cameras["cam_0"]
    assert camera.online is True
    assert camera.observations_received is None
    assert camera.calibrated is None


def test_pose_decodes_heading_from_euler_degrees():
    pose = PoseUpdate.from_body(_pose_body(euler_deg={"yaw": 90.0}))
    assert pose is not None
    assert pose.heading_rad == pytest.approx(math.pi / 2)
    assert (pose.x_m, pose.y_m, pose.z_m) == (1.0, 2.0, 0.5)


def test_pose_falls_back_to_the_quaternion_for_heading():
    half = math.sqrt(0.5)
    pose = PoseUpdate.from_body(
        _pose_body(orientation_xyzw={"x": 0.0, "y": 0.0, "z": half, "w": half})
    )
    assert pose is not None
    assert pose.heading_rad == pytest.approx(math.pi / 2)


@pytest.mark.parametrize(
    "body",
    [
        {"tag_id": 1},                                        # no position
        {"position_m": {"x": 1.0, "y": 2.0}},                 # no tag id
        {"tag_id": "sette", "position_m": {"x": 1.0, "y": 2.0}},
        {"tag_id": 1, "position_m": {"x": "left", "y": 2.0}},
    ],
)
def test_pose_returns_none_for_malformed_payloads(body):
    # The panel has to survive whatever a half-upgraded node publishes.
    assert PoseUpdate.from_body(body) is None


def test_event_separates_the_envelope_from_its_context():
    event = DeploymentEvent.from_body(
        {
            "code": "CALIBRATION_DRIFT",
            "message": "camera moved",
            "severity": "warning",
            "timestamp": 1234,
            "cameras": ["cam_2"],
        }
    )
    assert (event.code, event.severity) == ("CALIBRATION_DRIFT", "WARNING")
    assert event.context == {"cameras": ["cam_2"]}
    assert event.context_suffix() == ' {"cameras":["cam_2"]}'


def test_event_without_context_has_no_suffix():
    event = DeploymentEvent.from_body({"code": "X", "message": "m", "severity": "info"})
    assert event.context_suffix() == ""


def test_server_status_keeps_an_absent_online_flag_distinguishable():
    assert ServerStatus.from_body({"reason": "starting"}).online is None
    assert ServerStatus.from_body({"online": False}).online is False


def test_node_metrics_decode_the_capture_state_the_node_reports():
    message = parse(
        "vision/s/i/metrics",
        {
            "role": "node",
            "camera_id": "cam_2",
            "observations_published": 0,
            "capture_online": False,
            "capture_error": "cannot open source 3",
            "frames_received": 0,
            "source": "/dev/v4l/by-id/usb-Acme-video-index0",
            "calibrated": True,
            "detecting": False,
        },
    )
    assert message.capture_online is False
    assert message.capture_error == "cannot open source 3"
    assert message.frames_received == 0
    assert message.source == "/dev/v4l/by-id/usb-Acme-video-index0"
    assert message.calibrated is True
    assert message.detecting is False


def test_node_metrics_without_the_capture_fields_stay_undecided_about_them():
    """None is "this node does not say", which is not the same as "the camera is down"."""
    message = parse(
        "vision/s/i/metrics",
        {"role": "node", "camera_id": "cam_0", "observations_published": 3},
    )
    assert message.capture_online is None
    assert message.capture_error is None
    assert message.frames_received is None
    assert message.source is None


def test_an_empty_capture_error_is_no_error():
    message = parse(
        "vision/s/i/metrics",
        {"role": "node", "camera_id": "cam_0", "capture_online": True, "capture_error": None},
    )
    assert message.capture_error is None
