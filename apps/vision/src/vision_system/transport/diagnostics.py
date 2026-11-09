"""Structured JSON Lines diagnostic logging with automatic file rotation."""

from __future__ import annotations

import atexit
import json
import logging
import os
import platform
import sys
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import cv2

DEFAULT_DIAGNOSTIC_LOG = Path("diagnostics/vision-system.jsonl")
DIAGNOSTIC_MAX_BYTES = 20 * 1024 * 1024
DIAGNOSTIC_BACKUP_COUNT = 5
_SESSION_ID: str | None = None
_LOG_PATH: Path | None = None
_ATEXIT_REGISTERED = False


def _json_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return [_json_value(item) for item in sorted(value, key=str)]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
    return value


class DiagnosticJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "session_id": getattr(record, "diagnostic_session_id", _SESSION_ID),
        }
        event = getattr(record, "diagnostic_event", None)
        if event is not None:
            payload["event"] = event
            payload.update(_json_value(getattr(record, "diagnostic_fields", {})))
        else:
            payload["event"] = "log_message"
            payload["message"] = record.getMessage()
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_diagnostics(
    command: str,
    *,
    verbose: bool = False,
    log_path: Path = DEFAULT_DIAGNOSTIC_LOG,
    argv: list[str] | None = None,
) -> Path:
    """Configure readable console logs plus a persistent structured diagnostic log."""
    global _ATEXIT_REGISTERED, _LOG_PATH, _SESSION_ID
    _SESSION_ID = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    _LOG_PATH = log_path.resolve()
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        if getattr(handler, "vision_diagnostic_handler", False):
            root.removeHandler(handler)
            handler.close()

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.WARNING)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console.vision_diagnostic_handler = True  # type: ignore[attr-defined]
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        _LOG_PATH,
        maxBytes=DIAGNOSTIC_MAX_BYTES,
        backupCount=DIAGNOSTIC_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(DiagnosticJsonFormatter())
    file_handler.vision_diagnostic_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)

    event(
        logging.getLogger("vision_system.session"),
        "session_started",
        command=command,
        argv=list(sys.argv[1:] if argv is None else argv),
        cwd=Path.cwd(),
        pid=os.getpid(),
        python=sys.version.split()[0],
        platform=platform.platform(),
        opencv=cv2.__version__,
        log_path=_LOG_PATH,
    )

    def log_unhandled_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logging.getLogger("vision_system.session").critical(
            "unhandled_exception",
            exc_info=(exc_type, exc_value, traceback),
            extra={
                "diagnostic_event": "unhandled_exception",
                "diagnostic_fields": {"exception_type": exc_type.__name__},
                "diagnostic_session_id": _SESSION_ID,
            },
        )

    sys.excepthook = log_unhandled_exception
    if not _ATEXIT_REGISTERED:
        atexit.register(
            lambda: event(logging.getLogger("vision_system.session"), "session_finished")
        )
        _ATEXIT_REGISTERED = True
    return _LOG_PATH


def event(
    logger: logging.Logger,
    name: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    logger.log(
        level,
        name,
        extra={
            "diagnostic_event": name,
            "diagnostic_fields": _json_value(fields),
            "diagnostic_session_id": _SESSION_ID,
        },
    )


def log_path() -> Path | None:
    return _LOG_PATH
