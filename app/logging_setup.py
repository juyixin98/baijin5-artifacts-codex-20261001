"""Structured logging setup.

Every log line is emitted both to stderr (human readable) and, when
``RD_LOG_FILE`` is set, as a JSON object on its own line (machine readable).
Each estimation log carries the ``run_id`` so test/experiment output can be
correlated with the exact input run.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .config import settings


class JsonlHandler(logging.Handler):
    def __init__(self, path: Path) -> None:
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        payload: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if isinstance(extra, dict):
            payload["data"] = extra
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:  # logging must never crash the request path
            sys.stderr.write(f"jsonl log failure: {self.path}\n")


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("rd")
    if getattr(logger, "_configured", False):
        return logger
    logger.setLevel(settings.log_level)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    ))
    logger.addHandler(stderr)
    if settings.log_file is not None:
        logger.addHandler(JsonlHandler(settings.log_file))
    logger.propagate = False
    logger._configured = True  # type: ignore[attr-defined]
    return logger


def log_event(logger: logging.Logger, level: int, message: str, **data: Any) -> None:
    logger.log(level, message, extra={"data": data})
