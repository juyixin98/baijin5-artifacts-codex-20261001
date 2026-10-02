"""Structured-ish logging with request identity propagation.

Uses only the standard library. A :class:`contextvars.ContextVar` carries
the current request id so every log line emitted while handling a request
is correlated without threading the id through every call signature.
"""

from __future__ import annotations

import contextvars
import logging
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "edt_request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("edt_service")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [req=%(request_id)s] "
                "%(name)s.%(funcName)s: %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level)
    for handler in logger.handlers:
        handler.addFilter(RequestIdFilter())
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger("edt_service")
    if not logger.handlers:
        configure_logging()
    return logger
