"""Request-scoped structured diagnostics.

Every analysis run is bound to a ``request_id`` (client supplied or generated)
and emits JSON log lines carrying the request identity, service version and
the processing location (module/step). Failures and uncertain conclusions use
dedicated ``level`` values so they can be filtered out separately.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from app.config import settings

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def bind_request_id(request_id: str) -> contextvars.Token:
    return _request_id.set(request_id)


def current_request_id() -> str | None:
    return _request_id.get()


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "location": getattr(record, "location", record.funcName),
            "step": getattr(record, "step", None),
            "request_id": getattr(record, "request_id", None)
            or _request_id.get(),
            "version": settings.version,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload["context"] = _json_safe(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


_CONFIGURED_LOCK = threading.Lock()
_CONFIGURED = False


def configure_logging() -> logging.Logger:
    """Idempotently attach a JSON-lines file handler and a console handler."""
    global _CONFIGURED
    with _CONFIGURED_LOCK:
        logger = logging.getLogger("rct")
        if _CONFIGURED:
            return logger
        logger.setLevel(settings.log_level.upper())
        logger.propagate = False

        formatter = JsonLineFormatter()
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)

        log_path = Path(settings.log_path)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            # Logging must never break a request; console still works.
            logger.warning("could not open log file %s", log_path)

        _CONFIGURED = True
        return logger


def get_logger() -> logging.Logger:
    return configure_logging()


class StepLogger:
    """Emits one structured record per pipeline step for one request."""

    def __init__(self, request_id: str):
        self.request_id = request_id
        self.logger = get_logger()
        self.records: list[dict] = []

    def _emit(self, level: int, step: str, message: str,
              location: str, **context: object) -> None:
        record = {
            "step": step,
            "message": message,
            "location": location,
            "level": logging.getLevelName(level).lower(),
            "context": _json_safe(context),
        }
        self.records.append(record)
        self.logger.log(
            level,
            message,
            extra={
                "step": step,
                "location": location,
                "request_id": self.request_id,
                "context": context,
            },
        )

    def info(self, step: str, message: str, location: str, **context: object):
        self._emit(logging.INFO, step, message, location, **context)

    def warning(self, step: str, message: str, location: str, **context: object):
        self._emit(logging.WARNING, step, message, location, **context)

    def failure(self, step: str, message: str, location: str,
                code: str, **context: object):
        context = {**context, "failure_code": code}
        self._emit(logging.ERROR, step, message, location, **context)

    def uncertainty(self, step: str, message: str, location: str,
                    **context: object):
        """A conclusion that is approximate or unbounded, not a hard failure."""
        self._emit(logging.WARNING, step, message, location,
                   kind="uncertainty", **context)

    def trail(self) -> list[dict]:
        return list(self.records)
