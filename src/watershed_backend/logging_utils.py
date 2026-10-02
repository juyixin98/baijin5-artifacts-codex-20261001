"""Structured JSON logging correlated by run/job identity.

Every log record emitted during a job carries the job id, the SHA-256 of the
exact input arrays, the runtime versions and the current computation stage,
so a log line can always be traced back to the input that produced it and
the step that emitted it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

import numpy as np

_LOGGER_NAME = "watershed_backend"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("job_id", "run_id", "input_sha256", "stage", "event", "detail"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    return logger


def input_fingerprint(
    gradient: np.ndarray,
    markers: np.ndarray,
    connectivity: int,
    mask: np.ndarray | None,
) -> str:
    """SHA-256 over the exact bytes that determine the segmentation."""
    hasher = hashlib.sha256()
    hasher.update(np.ascontiguousarray(gradient, dtype=np.float64).tobytes())
    hasher.update(np.ascontiguousarray(markers, dtype=np.int32).tobytes())
    hasher.update(bytes([connectivity]))
    if mask is None:
        hasher.update(b"no-mask")
    else:
        hasher.update(np.ascontiguousarray(mask, dtype=bool).tobytes())
    return hasher.hexdigest()
