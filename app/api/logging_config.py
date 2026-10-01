"""Structured logging configuration.

Every log line is JSON with run_id / data_fingerprint where available so test
logs can be correlated to the input and run identity.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("run_id", "data_fingerprint", "step", "progress",
                    "versions", "error_code", "details"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", log_dir: str | Path | None = None) -> logging.Logger:
    logger = logging.getLogger("cuped")
    logger.handlers.clear()
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    if log_dir is not None:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path / "cuped.log", encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("cuped")
