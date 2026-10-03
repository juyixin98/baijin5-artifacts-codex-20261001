"""Structured JSON logging with request identity.

Every log line carries the request id, the pipeline step, and version
information so a run can be reconstructed from logs alone.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.version import ALGORITHM_VERSION, APP_VERSION

_LOGGER_NAME = "haplotype_phasing"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "step", "detail"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        payload["app_version"] = APP_VERSION
        payload["algorithm_version"] = ALGORITHM_VERSION
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def log_step(
    logger: logging.Logger,
    request_id: str,
    step: str,
    message: str,
    **detail: Any,
) -> None:
    logger.info(
        message,
        extra={"request_id": request_id, "step": step, "detail": detail or None},
    )
