"""Structured logging with request-identity correlation.

Every log record carries the current request id (via a context var set by
the HTTP middleware, or ``-`` outside a request) so a response, its log
lines and any failure can be traced back to one identity.
"""
from __future__ import annotations

import contextvars
import logging

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"
        )
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
