"""Structured logging with run/input correlation.

Every log line is JSON with service version and (when inside a digest run)
the run id and normalized input, so test and production logs can be tied back
to a concrete execution. Failures are logged as failures - never downgraded.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import __version__

_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_input_ref: ContextVar[str | None] = ContextVar("input_ref", default=None)


def bind_run(run_id: str | None, input_ref: str | None = None) -> Any:
    token1 = _run_id.set(run_id)
    token2 = _input_ref.set(input_ref)
    return token1, token2


def reset_run(tokens: Any) -> None:
    _run_id.reset(tokens[0])
    _input_ref.reset(tokens[1])


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service_version": __version__,
            "message": record.getMessage(),
        }
        run_id = _run_id.get()
        if run_id is not None:
            payload["run_id"] = run_id
        input_ref = _input_ref.get()
        if input_ref is not None:
            payload["input_ref"] = input_ref
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(log_dir: str, level: str = "INFO", to_file: bool = True) -> logging.Logger:
    logger = logging.getLogger("digest")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    logger.propagate = False

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(_JsonFormatter())
    logger.addHandler(stream)

    if to_file:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path / "digest.log", encoding="utf-8")
        file_handler.setFormatter(_JsonFormatter())
        logger.addHandler(file_handler)

    logger.info(
        "logging configured",
        extra={"extra_fields": {"event": "startup", "log_level": level.upper()}},
    )
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("digest")


class StepTimer:
    """Context manager logging a computation step with elapsed ms and verdict."""

    def __init__(self, step: str, **fields: Any) -> None:
        self.step = step
        self.fields = fields
        self._start = 0.0

    def __enter__(self) -> "StepTimer":
        self._start = time.perf_counter()
        get_logger().info(
            "step started",
            extra={"extra_fields": {"event": "step_start", "step": self.step, **self.fields}},
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = round((time.perf_counter() - self._start) * 1000, 3)
        if exc_type is None:
            get_logger().info(
                "step completed",
                extra={
                    "extra_fields": {
                        "event": "step_end",
                        "step": self.step,
                        "elapsed_ms": elapsed_ms,
                        "verdict": "OK",
                        **self.fields,
                    }
                },
            )
        else:
            get_logger().error(
                "step failed",
                extra={
                    "extra_fields": {
                        "event": "step_failed",
                        "step": self.step,
                        "elapsed_ms": elapsed_ms,
                        "verdict": "ERROR",
                        "error_type": exc_type.__name__,
                        **self.fields,
                    }
                },
                exc_info=(exc_type, exc, tb),
            )
