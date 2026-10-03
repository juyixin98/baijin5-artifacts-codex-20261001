"""Structured-ish stdlib logging with a request-id context variable.

Every log record emitted while handling a request carries the request id,
so kernel diagnostics can be correlated with the HTTP request that caused
them (contract: results and logs must be explainable per request).
"""

from __future__ import annotations

import contextvars
import logging
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s [req=%(request_id)s] %(message)s"
        )
    )
    handler.addFilter(_RequestIdFilter())
    root = logging.getLogger("opp495")
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"opp495.{name}")
