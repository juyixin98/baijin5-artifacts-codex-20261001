"""Structured run logging.

Every record is one JSON object per line carrying at least ``run_id``,
``step`` and ``reason`` so a failing run can be replayed from the log alone:
which pair was joined at which Q, why a tie broke the way it did, what was
clamped, and the final residual summary.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

_LOGGER_NAME = "nj_service"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "step": getattr(record, "step", record.getMessage() and "log"),
            "run_id": getattr(record, "run_id", None),
            "reason": record.getMessage(),
        }
        data = getattr(record, "data", None)
        if data:
            payload["data"] = data
        return json.dumps(payload, sort_keys=True)


def configure_logging(log_file: str | None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler: logging.Handler
        if log_file:
            handler = logging.FileHandler(log_file)
        else:
            handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)


def log_step(run_id: str, step: str, reason: str, data: dict[str, Any] | None = None) -> None:
    get_logger().info(
        reason, extra={"run_id": run_id, "step": step, "data": data or {}}
    )
