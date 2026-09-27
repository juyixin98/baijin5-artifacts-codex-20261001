"""Structured logging with per-request correlation ids."""

from __future__ import annotations

import contextvars
import logging
import sys

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")

from . import ENGINE_VERSION  # noqa: E402


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.job_id = job_id_var.get()
        record.engine_version = ENGINE_VERSION
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("fim")
    if logger.handlers:
        logger.setLevel(level)
        return logger
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s [req=%(request_id)s job=%(job_id)s] "
            "%(name)s@%(engine_version)s: %(message)s"
        )
    )
    handler.addFilter(_ContextFilter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("fim")
