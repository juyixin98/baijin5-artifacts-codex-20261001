"""Structured logging with per-request identity."""

from __future__ import annotations

import contextvars
import logging
import sys

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-")


def set_request_id(request_id: str) -> contextvars.Token:
    return _request_id.set(request_id)


def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("tensorcraft")
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s [req=%(request_id)s] %(name)s: "
        "%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
