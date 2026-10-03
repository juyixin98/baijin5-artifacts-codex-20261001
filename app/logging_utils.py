"""Logging setup: every record carries request identity and version."""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from app.version import __version__

#: request identity for the current context (set by API middleware,
#: or generated per pipeline call when used as a library)
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestContextFilter(logging.Filter):
    """Inject request_id and component version into every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        record.app_version = __version__
        return True


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("lpc_backend")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [version=%(app_version)s "
                "request_id=%(request_id)s] %(name)s: %(message)s"
            )
        )
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level.upper())
    for handler in logger.handlers:
        handler.addFilter(RequestContextFilter())
    return logger


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def get_logger() -> logging.Logger:
    return logging.getLogger("lpc_backend")
