"""Structured logging helpers.

Every log line is a JSON object carrying ``run_id`` when bound so that test and
service logs can be correlated to an input/run identity. Digest steps emit
progress (bonds scanned) and the evidence for each CUT/BLOCKED judgment.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone

_CONFIGURED_LOCK = threading.Lock()
_CONFIGURED = False


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None)
        if run_id:
            payload["run_id"] = run_id
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        extra = getattr(record, "fields", None)
        if extra:
            payload["fields"] = extra
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(log_dir: str, level: str = "INFO") -> None:
    """Configure root logging once; idempotent across test sessions."""
    global _CONFIGURED
    with _CONFIGURED_LOCK:
        if _CONFIGURED:
            return
        os.makedirs(log_dir, exist_ok=True)
        root = logging.getLogger()
        root.setLevel(level)
        formatter = JsonLineFormatter()

        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

        file_handler = logging.FileHandler(os.path.join(log_dir, "digest.log"), encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        _CONFIGURED = True


def add_log_file(path: str) -> logging.Handler:
    """Attach an extra file handler (used by the test suite / verify script)."""
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(JsonLineFormatter())
    logging.getLogger().addHandler(handler)
    return handler


class RunLogger:
    """Logger adapter that stamps every line with a run identity."""

    def __init__(self, name: str, run_id: str) -> None:
        self._logger = logging.getLogger(name)
        self.run_id = run_id

    def _log(self, level: int, event: str, message: str, **fields: object) -> None:
        self._logger.log(
            level,
            message,
            extra={"run_id": self.run_id, "event": event, "fields": fields},
        )

    def step(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.INFO, event, message, **fields)

    def progress(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.DEBUG, event, message, **fields)

    def judgment(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.INFO, event, message, **fields)

    def failure(self, event: str, message: str, **fields: object) -> None:
        self._log(logging.ERROR, event, message, **fields)
