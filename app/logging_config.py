"""Structured logging with request identity on every line."""
from __future__ import annotations

import logging

_FORMAT = "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger("motifscan")
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.addFilter(_RequestIdFilter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def get_request_logger(request_id: str) -> logging.LoggerAdapter:
    configure_logging()
    return logging.LoggerAdapter(logging.getLogger("motifscan"), {"request_id": request_id})
