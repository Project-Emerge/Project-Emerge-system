import json
from types import SimpleNamespace

import numpy as np

from vision_system.core.config import AppConfig
from vision_system.pipeline.fusion import FusedPose
from vision_system.transport.mqtt import (
    MqttSettings,
    OfflineBridge,
    config_update_payload,
    pose_payload,
    publish_config_update,
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
    pose = FusedPose(
        tag_id=4,
        monotonic_ns=1,
        utc_ns=1_700_000_000_000_000_000,
        world_from_tag=np.eye(4),
        position_m=np.array([1.0, 2.0, 3.0]),
        orientation_xyzw=np.array([0.0, 0.0, 0.0, 1.0]),
        velocity_m_s=np.zeros(3),
        angular_velocity_rad_s=np.zeros(3),
        cameras=["cam_0"],
        reprojection_error_px=0.5,
        quality=0.8,
    )
    payload = pose_payload(pose, sequence=17)
    assert payload["frame_id"] == "world"
    assert payload["sequence"] == 17
    assert payload["orientation_xyzw"]["w"] == 1.0
    json.dumps(payload, default=float)


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
