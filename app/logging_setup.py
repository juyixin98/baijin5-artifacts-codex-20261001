"""Structured JSON logging.

Every log record carries a run identity (``run_id`` / ``job_id``) and, where
applicable, the input digest (``input_sha256``) so test logs and service logs
can be correlated with the exact input and computation step that produced
them.  Versions are emitted once per run via ``log_run_started``.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.versions import version_snapshot

LOGGER_NAME = "seamcarve"

_DEFAULT_FIELDS = ("run_id", "job_id", "input_sha256", "event")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        for field in _DEFAULT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(level: str = "INFO", stream=None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    for handler in logger.handlers:
        handler.setLevel(level.upper())
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        configure_logging()
    return logger


def log_event(logger: logging.Logger, level: int, event: str, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"event": event, "fields": fields})


def log_run_started(logger: logging.Logger, *, run_id: str, input_sha256: str, **fields: Any) -> None:
    log_event(
        logger,
        logging.INFO,
        "run_started",
        f"run {run_id} started",
        run_id=run_id,
        input_sha256=input_sha256,
        versions=version_snapshot(),
        **fields,
    )
