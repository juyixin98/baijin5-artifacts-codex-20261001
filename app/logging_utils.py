"""Request-scoped structured logging.

Every log record carries the current request id so a run can be traced
end to end: request received -> engine/rounds -> graph/validation
outcome -> failures and uncertain conclusions.
"""

from __future__ import annotations

import contextvars
import logging
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def current_request_id() -> str:
    return _request_id.get()


def set_request_id(value: str | None = None) -> str:
    rid = value or uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s: %(message)s"
        )
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
