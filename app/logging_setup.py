"""Logging setup: every record carries the current request id.

The request id lives in a contextvar set by the HTTP middleware (or the
literal ``"-"`` outside request handling), so any log line emitted deep in
the signal code can be correlated back to the request that caused it.
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


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        )
    )
    # The filter must sit on the handler: logger-level filters do not apply
    # to records propagated from child loggers (e.g. uvicorn's).
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
