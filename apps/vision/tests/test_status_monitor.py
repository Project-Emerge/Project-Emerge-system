"""The read-only MQTT listener's callback surface."""

from vision_system.monitoring.listener import StatusMonitor


def test_monitor_subscribes_to_the_configured_base_topic():
    class FakeClient:
        def __init__(self) -> None:
            self.subscribed: list[str] = []

        def subscribe(self, topic, qos=0) -> None:
            self.subscribed.append(topic)

    fake = FakeClient()
    monitor = StatusMonitor("vision/lab/indoor-02", client_factory=lambda client_id: fake)
    monitor._on_connect(fake, None, None, 0, None)
    assert fake.subscribed == [
        "vision/lab/indoor-02/metrics",
        "vision/lab/indoor-02/event",
        "vision/lab/indoor-02/status",
        "vision/lab/indoor-02/pose/+",
    ]
    assert monitor.connected.is_set()


def test_monitor_reports_rejected_connections():
    class FakeClient:
        def subscribe(self, topic, qos=0) -> None:
            raise AssertionError("non deve sottoscrivere dopo un rifiuto")

    fake = FakeClient()
    monitor = StatusMonitor("vision/lab/indoor-02", client_factory=lambda client_id: fake)
    monitor._on_connect(fake, None, None, 5, None)
    assert monitor.connected.is_set() is False
    assert "5" in (monitor.error or "")


