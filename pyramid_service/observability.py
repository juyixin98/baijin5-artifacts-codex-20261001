"""Structured logging: every record is one JSON line carrying a run id.

Run ids let a failing build or request be replayed from the logs: each event
records the run id, the key intermediate state (level dims, tile counts,
checksums) and the reason for decisions (e.g. why a level count was chosen).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

LOGGER_NAME = "pyramid_service"


def new_run_id() -> str:
    """Short unique id attached to one build job or one request."""
    return uuid.uuid4().hex[:12]


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = True  # keep records visible to pytest's caplog
    return logger


def log_event(severity: int, event: str, run_id: str, **fields: Any) -> None:
    """Emit one JSON log record.

    ``fields`` should carry the intermediate state and decision rationale that
    make the event replayable (dimensions, checksums, limits, reasons).
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "run_id": run_id,
        **fields,
    }
    get_logger().log(severity, json.dumps(record, default=str, sort_keys=True))
