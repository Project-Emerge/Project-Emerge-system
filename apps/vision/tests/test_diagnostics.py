import json
import logging
from pathlib import Path

from vision_system.transport.diagnostics import DiagnosticJsonFormatter, _json_value


def test_diagnostic_formatter_writes_structured_event() -> None:
    record = logging.LogRecord("vision.test", logging.INFO, __file__, 1, "ignored", (), None)
    record.diagnostic_session_id = "session-1"
    record.diagnostic_event = "camera_checked"
    record.diagnostic_fields = {
        "camera_id": "cam_0",
        "shape": (1080, 1920, 3),
        "path": Path("config.local.json"),
    }

    payload = json.loads(DiagnosticJsonFormatter().format(record))

    assert payload["event"] == "camera_checked"
    assert payload["session_id"] == "session-1"
    assert payload["shape"] == [1080, 1920, 3]
    assert payload["path"] == "config.local.json"


def test_json_value_normalizes_nested_collections() -> None:
    assert _json_value({"ids": {3, 1}, "position": (1.0, 2.0)}) == {
        "ids": [1, 3],
        "position": [1.0, 2.0],
    }
