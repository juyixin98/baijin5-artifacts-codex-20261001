"""Structured JSON logging with request-id correlation.

Every log line emitted by the package carries the current ``request_id``
(or ``"-"`` outside of a request) so API results, stored lineage and log
output can be joined together.
"""
from __future__ import annotations

import contextvars
import datetime as _dt
import json
import logging

LOGGER_NAMESPACE = "nussinov_backend"

request_id_ctx: contextvars.ContextVar[str] = contextvars.ContextVar(
    "nussinov_request_id", default="-"
)


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return request_id_ctx.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    request_id_ctx.reset(token)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger(LOGGER_NAMESPACE)
    root.setLevel(level)
    root.addHandler(handler)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")
