"""Structured JSON logging with run identity.

Every log record emitted during a segmentation run carries a ``run_id`` so
test logs and service logs can be correlated back to a specific input (via
``input_sha256``) and run. The format is one JSON object per line.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_LOGGER_NAME = "watershed"


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        # Extra structured fields attached via ``extra={"fields": {...}}``.
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Emit one structured event. ``fields`` must be JSON-serialisable."""
    logger.log(level, event, extra={"fields": fields})
