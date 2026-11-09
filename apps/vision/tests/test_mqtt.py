import json
import math
from types import SimpleNamespace

import numpy as np

from vision_system.core.config import AppConfig
from vision_system.pipeline.fusion import FusedPose
from vision_system.transport.mqtt import (
    ARUCO_MAP_TOPIC,
    MqttBridge,
    MqttSettings,
    OfflineBridge,
    config_update_payload,
    dashboard_pose_payload,
    parse_aruco_robot_map,
    pose_payload,
    publish_config_update,
)


def _fused_pose(tag_id: int = 4) -> FusedPose:
    yaw = math.pi / 2
    world_from_tag = np.eye(4)
    world_from_tag[:3, :3] = np.array(
        [
            [math.cos(yaw), -math.sin(yaw), 0.0],
            [math.sin(yaw), math.cos(yaw), 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    return FusedPose(
        tag_id=tag_id,
        monotonic_ns=1,
        utc_ns=1_700_000_000_000_000_000,
        world_from_tag=world_from_tag,
        position_m=np.array([1.0, 2.0, 3.0]),
        orientation_xyzw=np.array([0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)]),
        velocity_m_s=np.array([3.0, 4.0, 0.5]),
        angular_velocity_rad_s=np.array([0.1, 0.2, 0.3]),
        cameras=["cam_0"],
        reprojection_error_px=0.5,
        quality=0.8,
    )


def test_config_update_payload_contains_revision_and_request_id() -> None:
    config = AppConfig(revision=12)
    payload = config_update_payload(config, "camera-settings-test")
    assert payload["request_id"] == "camera-settings-test"
    assert payload["config"]["revision"] == 12
    assert len(payload["config"]["cameras"]) == 4


def test_publish_config_update_waits_for_matching_result(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            self.on_connect = None
            self.on_message = None
            self.on_publish = None

        def connect(self, host, port, keepalive) -> None:
            self.on_connect(self, None, None, 0, None)

        def loop_start(self) -> None:
            return

        def loop_stop(self) -> None:
            return

        def disconnect(self) -> None:
            return

        def subscribe(self, topic, qos) -> None:
            self.result_topic = topic

        def publish(self, topic, payload, qos, retain):
            request = json.loads(payload)
            self.on_publish(self, None, 1, 0, None)
            result = {
                "request_id": request["request_id"],
                "accepted": True,
                "revision": request["config"]["revision"],
            }
            self.on_message(
                self,
                None,
                SimpleNamespace(topic=self.result_topic, payload=json.dumps(result).encode()),
            )
            return SimpleNamespace(rc=0)

    monkeypatch.setattr("vision_system.transport.mqtt.mqtt.Client", FakeClient)
    response = publish_config_update(
        AppConfig(revision=8),
        settings=MqttSettings(host="broker.test", client_id="test"),
        timeout=0.1,
    )
    assert response == {"request_id": response["request_id"], "accepted": True, "revision": 8}


def test_pose_payload_uses_world_frame_and_xyzw() -> None:
    pose = _fused_pose()
    payload = pose_payload(pose, sequence=17)
    assert payload["frame_id"] == "world"
    assert payload["sequence"] == 17
    assert payload["orientation_xyzw"]["w"] == pose.orientation_xyzw[3]
    assert payload["velocity_m_s"] == {"x": 3.0, "y": 4.0, "z": 0.5}
    assert payload["angular_velocity_rad_s"] == {"x": 0.1, "y": 0.2, "z": 0.3}
    json.dumps(payload, default=float)


def test_dashboard_pose_payload_contains_only_known_compatibility_fields() -> None:
    payload = dashboard_pose_payload(_fused_pose(), sequence=7)
    assert payload["x_m"] == 1.0
    assert payload["y_m"] == 2.0
    assert payload["z_m"] == 3.0
    assert payload["roll_rad"] == 0.0
    assert payload["pitch_rad"] == 0.0
    assert payload["heading_rad"] == math.pi / 2
    assert payload["speed_m_s"] == 5.0
    assert payload["timestamp_us"] == 1_700_000_000_000_000
    assert payload["sequence"] == 7
    assert payload["quality"] == 0.8
    assert payload["visible_by"] == ["cam_0"]
    assert "position_variance_m2" not in payload


def test_parse_aruco_robot_map_validates_marker_and_robot_ids() -> None:
    assert parse_aruco_robot_map({"0": "A1B2C3", "12": "D4E5F6"}) == {
        0: "A1B2C3",
        12: "D4E5F6",
    }
    for invalid in (
        {"01": "A1B2C3"},
        {"50": "A1B2C3"},
        {"1": "invalid"},
        {"1": "A1B2C3", "2": "A1B2C3"},
    ):
        try:
            parse_aruco_robot_map(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"mapping should be rejected: {invalid}")


def test_mqtt_bridge_subscribes_to_mapping_and_publishes_both_pose_topics(
    tmp_path,
) -> None:
    bridge = MqttBridge(AppConfig(), tmp_path / "cache.json", tmp_path / "calibrations")
    subscriptions = []
    published = []
    client = SimpleNamespace(
        subscribe=lambda topic, qos: subscriptions.append((topic, qos)),
    )
    bridge._publish_json = lambda topic, body, qos=0, retain=False: published.append(
        (topic, body, qos, retain)
    )

    bridge._on_connect(client, None, None, 0, None)
    assert (ARUCO_MAP_TOPIC, 1) in subscriptions

    bridge._on_message(
        client,
        None,
        SimpleNamespace(topic=ARUCO_MAP_TOPIC, payload=b'{"4":"A1B2C3"}'),
    )
    published.clear()
    bridge.publish_pose(_fused_pose())
    assert [item[0] for item in published] == [
        "vision/default/indoor-01/pose/4",
        "/pose/A1B2C3",
    ]
    dashboard_payload = published[1][1]
    assert dashboard_payload["tag_id"] == 4
    assert dashboard_payload["x_m"] == 1.0
    assert dashboard_payload["heading_rad"] == math.pi / 2
    assert "position_variance_m2" not in dashboard_payload


def test_offline_bridge_prints_pose_without_network(capsys) -> None:
    pose = FusedPose(
        tag_id=9,
        monotonic_ns=1,
        utc_ns=1_700_000_000_000_000_000,
        world_from_tag=np.eye(4),
        position_m=np.array([1.0, 2.0, 3.0]),
        orientation_xyzw=np.array([0.0, 0.0, 0.0, 1.0]),
        velocity_m_s=np.zeros(3),
        angular_velocity_rad_s=np.zeros(3),
        cameras=["cam_0"],
        reprojection_error_px=0.2,
        quality=0.9,
    )
    bridge = OfflineBridge(AppConfig(), print_poses=True)
    bridge.start()
    bridge.publish_pose(pose)
    bridge.publish_pose(pose)
    lines = capsys.readouterr().out.splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == [0, 1]
