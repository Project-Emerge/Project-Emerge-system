"""Read-only MQTT listener for the deployment panel."""

from __future__ import annotations

import json
import queue
import threading
import uuid
from collections.abc import Callable

import paho.mqtt.client as mqtt

from ..transport.mqtt import MQTT_KEEPALIVE_S, MqttSettings


class StatusMonitor:
    """Read-only MQTT listener: never publishes, so it cannot disturb the deployment."""

    def __init__(
        self,
        base_topic: str,
        settings: MqttSettings | None = None,
        client_factory: Callable[[str], mqtt.Client] | None = None,
    ) -> None:
        self.base_topic = base_topic
        self.settings = settings or MqttSettings.from_environment()
        self.connected = threading.Event()
        self.error: str | None = None
        self.messages: queue.SimpleQueue[tuple[str, dict]] = queue.SimpleQueue()
        client_id = f"vision-gui-{uuid.uuid4().hex[:10]}"
        self.client = (
            client_factory(client_id)
            if client_factory
            else mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                protocol=mqtt.MQTTv311,
                reconnect_on_failure=True,
            )
        )
        if self.settings.username:
            self.client.username_pw_set(self.settings.username, self.settings.password)
        if self.settings.tls:
            self.client.tls_set()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self._loop_started = False

    def topics(self) -> list[str]:
        return [
            f"{self.base_topic}/metrics",
            f"{self.base_topic}/event",
            f"{self.base_topic}/status",
            f"{self.base_topic}/pose/+",
        ]

    def start(self) -> None:
        try:
            self.client.connect_async(
                self.settings.host, self.settings.port, keepalive=MQTT_KEEPALIVE_S
            )
            self.client.loop_start()
            self._loop_started = True
        except OSError as error:
            self.error = str(error)

    def stop(self) -> None:
        try:
            self.client.disconnect()
        finally:
            if self._loop_started:
                self.client.loop_stop()
                self._loop_started = False
            self.connected.clear()

    def drain(self, limit: int = 500) -> list[tuple[str, dict]]:
        received: list[tuple[str, dict]] = []
        while len(received) < limit:
            try:
                received.append(self.messages.get_nowait())
            except queue.Empty:
                break
        return received

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code != 0:
            self.error = f"connessione MQTT rifiutata: {reason_code}"
            return
        self.error = None
        self.connected.set()
        for topic in self.topics():
            client.subscribe(topic, qos=0)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self.connected.clear()

    def _on_message(self, client, userdata, message) -> None:
        try:
            body = json.loads(message.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(body, dict):
            self.messages.put((message.topic, body))
