"""MQTT transport bridge for publishing fused poses/metrics and receiving configs."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import paho.mqtt.client as mqtt
from pydantic import ValidationError

from ..core.config import AppConfig, CameraCalibration, save_json
from ..pipeline.fusion import FusedPose

LOGGER = logging.getLogger(__name__)
MQTT_KEEPALIVE_S = 30
ARUCO_MAP_TOPIC = "/config/aruco-map"
DEVICE_ID_PATTERN = re.compile(r"^[A-F0-9]{6}$")
ARUCO_MARKER_ID_MIN = 0
ARUCO_MARKER_ID_MAX = 49


@dataclass(frozen=True)
class MqttSettings:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    client_id: str = ""

    @classmethod
    def from_environment(cls) -> MqttSettings:
        return cls(
            host=os.getenv("VISION_MQTT_HOST", "localhost"),
            port=int(os.getenv("VISION_MQTT_PORT", "1883")),
            username=os.getenv("VISION_MQTT_USERNAME"),
            password=os.getenv("VISION_MQTT_PASSWORD"),
            tls=os.getenv("VISION_MQTT_TLS", "false").lower() in {"1", "true", "yes"},
            client_id=os.getenv("VISION_MQTT_CLIENT_ID", f"vision-{uuid.uuid4().hex[:10]}"),
        )


def _iso_from_ns(timestamp_ns: int) -> str:
    return datetime.fromtimestamp(timestamp_ns / 1e9, UTC).isoformat(timespec="milliseconds")


def config_update_payload(config: AppConfig, request_id: str | None = None) -> dict:
    """Build the envelope accepted by ``config/set``."""
    return {
        "request_id": request_id or f"camera-config-{uuid.uuid4().hex[:12]}",
        "config": config.model_dump(mode="json"),
    }


def publish_config_update(
    config: AppConfig,
    settings: MqttSettings | None = None,
    timeout: float = 5.0,
) -> dict | None:
    """Publish a retained complete configuration and wait briefly for validation.

    ``None`` means the broker accepted the publish but no running VisionSystem
    returned a validation result before the timeout.
    """
    selected_settings = settings or MqttSettings.from_environment()
    envelope = config_update_payload(config)
    result_topic = f"{config.base_topic}/config/result"
    set_topic = f"{config.base_topic}/config/set"
    response: dict = {}
    sent = threading.Event()
    published = threading.Event()
    received = threading.Event()
    publish_error: list[str] = []
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=selected_settings.client_id or f"vision-config-{uuid.uuid4().hex[:10]}",
        protocol=mqtt.MQTTv311,
    )
    if selected_settings.username:
        client.username_pw_set(selected_settings.username, selected_settings.password)
    if selected_settings.tls:
        client.tls_set()

    def on_connect(client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            publish_error.append(f"MQTT connection rejected: {reason_code}")
            sent.set()
            return
        client.subscribe(result_topic, qos=1)
        info = client.publish(
            set_topic,
            json.dumps(envelope, separators=(",", ":")),
            qos=1,
            retain=True,
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            publish_error.append(f"MQTT publish failed: {mqtt.error_string(info.rc)}")
        sent.set()

    def on_message(client, userdata, message) -> None:
        if message.topic != result_topic:
            return
        try:
            body = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if body.get("request_id") == envelope["request_id"]:
            response.update(body)
            received.set()

    def on_publish(client, userdata, mid, reason_code, properties) -> None:
        published.set()

    client.on_connect = on_connect
    client.on_message = on_message
    client.on_publish = on_publish
    try:
        client.connect(
            selected_settings.host,
            selected_settings.port,
            keepalive=MQTT_KEEPALIVE_S,
        )
        client.loop_start()
        if not sent.wait(timeout):
            raise TimeoutError("timeout durante la pubblicazione MQTT")
        if publish_error:
            raise ConnectionError(publish_error[0])
        if not published.wait(timeout):
            raise TimeoutError("il broker non ha confermato la pubblicazione MQTT")
        received.wait(timeout)
        return response or None
    finally:
        try:
            client.disconnect()
        finally:
            client.loop_stop()


def _euler_radians(rotation: np.ndarray) -> dict[str, float]:
    sy = float(np.hypot(rotation[0, 0], rotation[1, 0]))
    singular = sy < 1e-8
    if not singular:
        roll = np.arctan2(rotation[2, 1], rotation[2, 2])
        pitch = np.arctan2(-rotation[2, 0], sy)
        yaw = np.arctan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = np.arctan2(-rotation[1, 2], rotation[1, 1])
        pitch = np.arctan2(-rotation[2, 0], sy)
        yaw = 0.0
    return {"roll": float(roll), "pitch": float(pitch), "yaw": float(yaw)}


def _euler_degrees(rotation: np.ndarray) -> dict[str, float]:
    radians = _euler_radians(rotation)
    return {
        axis: round(float(np.degrees(angle)), 4) for axis, angle in radians.items()
    }


def pose_payload(pose: FusedPose, sequence: int = 0) -> dict:
    now_ns = time.monotonic_ns()
    position = pose.position_m
    quaternion = pose.orientation_xyzw
    return {
        "schema_version": 1,
        "tag_id": pose.tag_id,
        "frame_id": "world",
        "sequence": sequence,
        "captured_at": _iso_from_ns(pose.utc_ns),
        "published_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "position_m": {"x": position[0], "y": position[1], "z": position[2]},
        "orientation_xyzw": {
            "x": quaternion[0],
            "y": quaternion[1],
            "z": quaternion[2],
            "w": quaternion[3],
        },
        "velocity_m_s": {
            "x": pose.velocity_m_s[0],
            "y": pose.velocity_m_s[1],
            "z": pose.velocity_m_s[2],
        },
        "angular_velocity_rad_s": {
            "x": pose.angular_velocity_rad_s[0],
            "y": pose.angular_velocity_rad_s[1],
            "z": pose.angular_velocity_rad_s[2],
        },
        "euler_deg": _euler_degrees(pose.world_from_tag[:3, :3]),
        "visible_by": pose.cameras,
        "reprojection_error_px": pose.reprojection_error_px,
        "quality": pose.quality,
        "predicted": pose.predicted,
        "age_ms": max(0.0, (now_ns - pose.monotonic_ns) / 1e6),
        "valid": True,
    }


def dashboard_pose_payload(pose: FusedPose, sequence: int = 0) -> dict:
    """Adapt all known VisionSystem pose data to the dashboard contract."""
    angles = _euler_radians(pose.world_from_tag[:3, :3])
    payload = pose_payload(pose, sequence)
    payload.update(
        {
            "x_m": pose.position_m[0],
            "y_m": pose.position_m[1],
            "z_m": pose.position_m[2],
            "roll_rad": angles["roll"],
            "pitch_rad": angles["pitch"],
            "heading_rad": angles["yaw"],
            "speed_m_s": float(np.linalg.norm(pose.velocity_m_s[:2])),
            "timestamp_us": pose.utc_ns // 1_000,
        }
    )
    return payload


def parse_aruco_robot_map(payload: object) -> dict[int, str]:
    """Validate the dashboard's retained marker-to-device mapping."""
    if not isinstance(payload, dict):
        raise ValueError("ArUco map must be a JSON object")
    mapping: dict[int, str] = {}
    device_ids: set[str] = set()
    for raw_marker_id, raw_device_id in payload.items():
        if not isinstance(raw_marker_id, str) or not raw_marker_id.isdigit():
            raise ValueError(f"invalid ArUco marker ID: {raw_marker_id!r}")
        marker_id = int(raw_marker_id)
        if str(marker_id) != raw_marker_id or not (
            ARUCO_MARKER_ID_MIN <= marker_id <= ARUCO_MARKER_ID_MAX
        ):
            raise ValueError(f"invalid ArUco marker ID: {raw_marker_id!r}")
        if not isinstance(raw_device_id, str) or not DEVICE_ID_PATTERN.fullmatch(
            raw_device_id
        ):
            raise ValueError(f"invalid robot device ID for marker {marker_id}")
        if raw_device_id in device_ids:
            raise ValueError(f"robot device ID mapped more than once: {raw_device_id}")
        mapping[marker_id] = raw_device_id
        device_ids.add(raw_device_id)
    return mapping


class OfflineBridge:
    """MQTT-compatible sink used when networking is explicitly disabled."""

    def __init__(
        self,
        config: AppConfig,
        print_poses: bool = False,
        print_observations: bool = False,
    ) -> None:
        self.config = config
        self.print_poses = print_poses
        self.print_observations = print_observations
        self.connected = threading.Event()
        self.pose_sequences: dict[int, int] = {}
        self.observation_sequences: dict[str, int] = {}

    def start(self) -> None:
        LOGGER.info("MQTT disabled: running in offline mode")

    def stop(self) -> None:
        return

    def wait_for_config(self, timeout: float = 0) -> AppConfig:
        return self.config

    def publish_pose(self, pose: FusedPose) -> None:
        sequence = self.pose_sequences.get(pose.tag_id, 0)
        if self.print_poses:
            print(json.dumps(pose_payload(pose, sequence), separators=(",", ":"), default=float))
        self.pose_sequences[pose.tag_id] = sequence + 1

    def publish_observations(self, camera_id: str, payload: dict) -> None:
        sequence = self.observation_sequences.get(camera_id, 0)
        body = {"sequence": sequence, **payload}
        if self.print_observations:
            print(json.dumps(body, separators=(",", ":"), default=float))
        self.observation_sequences[camera_id] = sequence + 1

    def publish_status(self, online: bool, reason: str) -> None:
        LOGGER.debug("offline status: online=%s reason=%s", online, reason)

    def publish_metrics(self, metrics: dict) -> None:
        return

    def publish_event(self, code: str, message: str, severity: str = "warning", **context) -> None:
        level = logging.ERROR if severity == "error" else logging.INFO
        LOGGER.log(level, "%s: %s %s", code, message, context or "")

    def publish_camera_status(self, camera_id: str, status: dict) -> None:
        return

    def publish_calibration(self, calibration: CameraCalibration) -> None:
        return

    def publish_calibration_session(self, body: dict) -> None:
        return


class MqttBridge:
    def __init__(
        self,
        initial_config: AppConfig,
        cache_path: Path,
        calibration_dir: Path,
        on_config: Callable[[AppConfig], None] | None = None,
        settings: MqttSettings | None = None,
        extra_subscriptions: dict[str, int] | None = None,
        on_extra_message: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.config = initial_config
        self.cache_path = cache_path
        self.calibration_dir = calibration_dir
        self.on_config_callback = on_config
        self.extra_subscriptions = dict(extra_subscriptions or {})
        self.on_extra_message = on_extra_message
        self.settings = settings or MqttSettings.from_environment()
        self.config_event = threading.Event()
        self.connected = threading.Event()
        self.pose_sequences: dict[int, int] = {}
        self.robot_ids_by_tag: dict[int, str] = {}
        self.loop_started = False
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.settings.client_id,
            protocol=mqtt.MQTTv311,
            reconnect_on_failure=True,
        )
        if self.settings.username:
            self.client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.tls:
            self.client.tls_set()
        self.client.will_set(
            f"{self.config.base_topic}/status",
            json.dumps({"online": False, "reason": "connection_lost"}),
            qos=1,
            retain=True,
        )
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        try:
            self.client.connect_async(
                self.settings.host,
                self.settings.port,
                keepalive=MQTT_KEEPALIVE_S,
            )
            self.client.loop_start()
            self.loop_started = True
        except OSError as error:
            LOGGER.error("MQTT network loop unavailable; continuing offline: %s", error)

    def stop(self) -> None:
        if self.connected.is_set():
            self.publish_status(False, "shutdown")
            self.client.disconnect()
        if self.loop_started:
            self.client.loop_stop()
            self.loop_started = False

    def wait_for_config(self, timeout: float = 2.0) -> AppConfig:
        self.config_event.wait(timeout)
        return self.config

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            LOGGER.error("MQTT connection rejected: %s", reason_code)
            return
        self.connected.set()
        base = self.config.base_topic
        client.subscribe(f"{base}/config/set", qos=1)
        client.subscribe(f"{base}/calibration/+/set", qos=1)
        client.subscribe(ARUCO_MAP_TOPIC, qos=1)
        for topic, qos in self.extra_subscriptions.items():
            client.subscribe(topic, qos=qos)
        self.publish_status(True, "running")
        self._publish_json(
            f"{base}/config/state", self.config.model_dump(mode="json"), qos=1, retain=True
        )

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected.clear()
        if reason_code != 0:
            LOGGER.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, message) -> None:
        try:
            body = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            self.publish_event("INVALID_JSON", str(error), severity="error")
            return
        if message.topic == ARUCO_MAP_TOPIC:
            self._accept_aruco_map(body)
        elif message.topic.endswith("/config/set"):
            self._accept_config(body)
        elif "/calibration/" in message.topic and message.topic.endswith("/set"):
            self._accept_calibration(message.topic, body)
        elif self.on_extra_message is not None:
            self.on_extra_message(message.topic, body)

    def _accept_config(self, body: dict) -> None:
        request_id = str(body.get("request_id", uuid.uuid4()))
        payload = body.get("config", body)
        payload.pop("request_id", None)
        topic = f"{self.config.base_topic}/config/result"
        try:
            candidate = AppConfig.model_validate(payload)
            if candidate.revision <= self.config.revision:
                raise ValueError(
                    f"revision must increase ({candidate.revision} <= {self.config.revision})"
                )
            old_base = self.config.base_topic
            if candidate.base_topic != old_base:
                self.client.unsubscribe(f"{old_base}/config/set")
                self.client.unsubscribe(f"{old_base}/calibration/+/set")
                for topic in self.extra_subscriptions:
                    self.client.unsubscribe(topic)
                self.client.subscribe(f"{candidate.base_topic}/config/set", qos=1)
                self.client.subscribe(f"{candidate.base_topic}/calibration/+/set", qos=1)
                self.extra_subscriptions = {
                    topic.replace(old_base, candidate.base_topic, 1): qos
                    for topic, qos in self.extra_subscriptions.items()
                }
                for topic, qos in self.extra_subscriptions.items():
                    self.client.subscribe(topic, qos=qos)
            if self.on_config_callback:
                self.on_config_callback(candidate)
            self.config = candidate
            save_json(self.cache_path, candidate)
            self._publish_json(
                f"{candidate.base_topic}/config/state",
                candidate.model_dump(mode="json"),
                qos=1,
                retain=True,
            )
            self._publish_json(
                f"{candidate.base_topic}/config/result",
                {"request_id": request_id, "accepted": True, "revision": candidate.revision},
                qos=1,
            )
            self.config_event.set()
        except (ValidationError, ValueError) as error:
            self._publish_json(
                topic,
                {"request_id": request_id, "accepted": False, "error": str(error)},
                qos=1,
            )

    def _accept_calibration(self, topic: str, body: dict) -> None:
        camera_id = topic.split("/calibration/", 1)[1].rsplit("/set", 1)[0]
        try:
            calibration = CameraCalibration.model_validate(body.get("calibration", body))
            if calibration.camera_id != camera_id:
                raise ValueError("camera id in topic and payload differ")
            save_json(self.calibration_dir / f"{camera_id}.json", calibration)
            self.publish_calibration(calibration)
        except (ValidationError, ValueError) as error:
            self.publish_event(
                "INVALID_CALIBRATION", str(error), severity="error", camera_id=camera_id
            )

    def _accept_aruco_map(self, body: object) -> None:
        try:
            mapping = parse_aruco_robot_map(body)
        except ValueError as error:
            self.publish_event("INVALID_ARUCO_MAP", str(error), severity="error")
            return
        self.robot_ids_by_tag = mapping
        LOGGER.info("ArUco robot mapping updated: %s", mapping)

    def _publish_json(self, topic: str, body: dict, qos: int = 0, retain: bool = False) -> None:
        self.client.publish(
            topic,
            json.dumps(body, separators=(",", ":"), default=float),
            qos=qos,
            retain=retain,
        )

    def publish_pose(self, pose: FusedPose) -> None:
        sequence = self.pose_sequences.get(pose.tag_id, 0)
        self._publish_json(
            f"{self.config.base_topic}/pose/{pose.tag_id}", pose_payload(pose, sequence)
        )
        if device_id := self.robot_ids_by_tag.get(pose.tag_id):
            self._publish_json(
                f"/pose/{device_id}", dashboard_pose_payload(pose, sequence)
            )
        self.pose_sequences[pose.tag_id] = sequence + 1

    def publish_observations(self, camera_id: str, payload: dict) -> None:
        self._publish_json(f"{self.config.base_topic}/observations/{camera_id}", payload)

    def publish_status(self, online: bool, reason: str) -> None:
        self._publish_json(
            f"{self.config.base_topic}/status",
            {"online": online, "reason": reason, "timestamp": datetime.now(UTC).isoformat()},
            qos=1,
            retain=True,
        )

    def publish_metrics(self, metrics: dict) -> None:
        self._publish_json(f"{self.config.base_topic}/metrics", metrics)

    def publish_event(self, code: str, message: str, severity: str = "warning", **context) -> None:
        self._publish_json(
            f"{self.config.base_topic}/event",
            {
                "code": code,
                "message": message,
                "severity": severity,
                "timestamp": datetime.now(UTC).isoformat(),
                **context,
            },
            qos=1,
        )

    def publish_camera_status(self, camera_id: str, status: dict) -> None:
        self._publish_json(f"{self.config.base_topic}/camera/{camera_id}/status", status, qos=1)

    def publish_calibration(self, calibration: CameraCalibration) -> None:
        self._publish_json(
            f"{self.config.base_topic}/calibration/{calibration.camera_id}/state",
            calibration.model_dump(mode="json"),
            qos=1,
            retain=True,
        )

    def publish_calibration_session(self, body: dict) -> None:
        self._publish_json(f"{self.config.base_topic}/calibration/session", body, qos=1)
