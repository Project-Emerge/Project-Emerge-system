"""Wording: every model -> text decision the panel makes."""

import pytest

from vision_system.apps.server_gui import presenters, strings
from vision_system.apps.server_gui.models import Notice, NoticeCode, PanelSnapshot
from vision_system.monitoring.deployment import CameraIssue, CameraRow
from vision_system.transport.payloads import DeploymentEvent, ServerStatus, parse


def _snapshot(**overrides):
    base = dict(
        now_ns=0,
        process_running=False,
        pid=None,
        exit_code=None,
        broker=None,
        broker_connected=False,
        broker_error=None,
        coordinator_online=False,
        poses_published=0,
        tracked_tags=(),
        rows=(),
    )
    base.update(overrides)
    return PanelSnapshot(**base)


def _row(**overrides):
    base = dict(
        camera_id="cam_0",
        node_online=True,
        node_observations=120,
        server_online=True,
        observations_received=118,
        age_ms=12.5,
        calibrated=True,
        issues=(),
    )
    base.update(overrides)
    return CameraRow(**base)


def _cells(row) -> dict[str, str]:
    """The rendered row keyed by column name, so a new column moves nothing."""
    values = presenters.roster_values(row)
    assert len(values) == len(strings.ROSTER_COLUMNS)
    return {
        name: value
        for (name, _, _), value in zip(strings.ROSTER_COLUMNS, values, strict=True)
    }


def test_server_indicator_distinguishes_running_stopped_and_exited():
    assert "pid 7" in presenters.indicator_texts(
        _snapshot(process_running=True, pid=7)
    ).server
    assert presenters.indicator_texts(_snapshot()).server == strings.SERVER_STOPPED
    assert "3" in presenters.indicator_texts(_snapshot(exit_code=3)).server


def test_broker_indicator_reports_connection_error_and_waiting_states():
    assert presenters.indicator_texts(_snapshot()).broker == strings.BROKER_DISCONNECTED
    connecting = _snapshot(broker=("h", 1883))
    assert presenters.indicator_texts(connecting).broker == strings.BROKER_CONNECTING
    failed = _snapshot(broker=("h", 1883), broker_error="mqtt connection refused: 5")
    assert "5" in presenters.indicator_texts(failed).broker
    live = _snapshot(broker=("h", 1883), broker_connected=True, coordinator_online=True)
    assert "h:1883" in presenters.indicator_texts(live).broker
    assert strings.FUSION_ACTIVE in presenters.indicator_texts(live).broker


def test_fusion_indicator_summarises_poses_and_tags():
    text = presenters.indicator_texts(_snapshot(poses_published=42, tracked_tags=(7, 9))).fusion
    assert "42" in text and "7, 9" in text
    assert strings.UNKNOWN in presenters.indicator_texts(_snapshot()).fusion


def test_roster_values_follow_the_declared_column_order():
    cells = _cells(_row())
    assert cells["camera"] == "cam_0"
    assert cells["calibrated"] == strings.YES
    assert cells["received"] == "118"


@pytest.mark.parametrize(
    ("calibrated", "expected"),
    [(True, strings.YES), (False, strings.NO), (None, strings.UNKNOWN)],
)
def test_unknown_calibration_is_distinct_from_a_negative_one(calibrated, expected):
    assert _cells(_row(calibrated=calibrated))["calibrated"] == expected


def test_missing_counters_render_as_unknown_not_zero():
    cells = _cells(_row(node_observations=None, age_ms=None))
    assert cells["published"] == strings.UNKNOWN
    assert cells["age"] == strings.UNKNOWN


def test_a_node_that_does_not_report_its_capture_leaves_the_column_unknown():
    """An older node publishes no capture field; that is not the same as a dead camera."""
    assert _cells(_row())["capture"] == strings.UNKNOWN
    assert _cells(_row())["frames"] == strings.UNKNOWN


def test_the_capture_column_shows_the_state_and_the_source_the_node_opened():
    cells = _cells(_row(capture_online=True, source=3, frames_received=412))
    assert cells["capture"] == f"{presenters.format_flag(True)} source 3"
    assert cells["frames"] == "412"


def test_a_capture_failure_is_reported_verbatim_next_to_its_code():
    """"cannot open source 3" names the device; a code alone would lose the number."""
    row = _row(
        issues=(CameraIssue.CAMERA_NOT_CAPTURING,),
        capture_online=False,
        capture_error="cannot open source 3",
    )
    notes = _cells(row)["issues"]
    assert strings.ISSUE_TEXT[CameraIssue.CAMERA_NOT_CAPTURING] in notes
    assert "cannot open source 3" in notes


def test_issues_are_joined_in_the_order_the_model_reported_them():
    row = _row(issues=(CameraIssue.DRIFT_RECALIBRATE, CameraIssue.NODE_UP_NO_OBSERVATIONS))
    rendered = _cells(row)["issues"]
    assert rendered == "; ".join(
        (
            strings.ISSUE_TEXT[CameraIssue.DRIFT_RECALIBRATE],
            strings.ISSUE_TEXT[CameraIssue.NODE_UP_NO_OBSERVATIONS],
        )
    )


def test_every_issue_code_has_wording():
    assert set(strings.ISSUE_TEXT) == set(CameraIssue)


def test_every_notice_code_has_wording():
    assert set(presenters.NOTICE_TEXT) == set(NoticeCode)


def test_event_line_keeps_its_compact_json_context():
    event = DeploymentEvent.from_body(
        {
            "code": "MISSING_CALIBRATION",
            "message": "Cameras without calibration",
            "severity": "warning",
            "cameras": ["cam_2"],
        }
    )
    assert presenters.describe_message(event) == (
        '[WARNING] MISSING_CALIBRATION: Cameras without calibration {"cameras":["cam_2"]}'
    )


def test_status_line_omits_an_empty_reason():
    assert presenters.describe_message(ServerStatus.from_body({"online": True})) == (
        "[status] online=True"
    )


def test_metrics_are_not_logged():
    metrics = parse("x/metrics", {"role": "coordinator"})
    assert presenters.describe_message(metrics) is None


def test_notice_wording_lists_the_cameras_it_is_about():
    notice = Notice(NoticeCode.LISTENING, {"topic": "vision/lab/x", "cameras": ["cam_0", "cam_1"]})
    rendered = presenters.format_notice(notice)
    assert "vision/lab/x" in rendered and "cam_0, cam_1" in rendered


def test_notice_wording_names_an_empty_roster_explicitly():
    notice = Notice(NoticeCode.LISTENING, {"topic": "t", "cameras": []})
    assert strings.NO_CAMERAS in presenters.format_notice(notice)
