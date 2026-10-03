"""Structured run logging.

Every processing/evaluation request gets a ``run_id`` so a failing run can
be replayed from the logs: the log record carries the run id, the key
intermediate states (weight norms before/after, reference energy, minimum
NLMS denominator) and the rationale for decisions (frozen intervals,
regularization engagement, validation rejections).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

LOGGER_NAME = "opp506"


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = getattr(record, "run_id", None)
        if run_id is not None:
            payload["run_id"] = run_id
        context = getattr(record, "context", None)
        if context:
            payload["context"] = context
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = get_logger()
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_run(logger: logging.Logger, run_id: str, message: str, **context: Any) -> None:
    """Emit one structured run record with replay-relevant state."""
    logger.info(message, extra={"run_id": run_id, "context": context})
