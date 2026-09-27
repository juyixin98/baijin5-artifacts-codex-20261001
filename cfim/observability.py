"""Request correlation and structured JSON logging.

Every log line is a single JSON object carrying:

* ``request_id`` - echoed from the inbound ``X-Request-ID`` header or
  generated, and returned in the response envelope/header;
* ``step`` - the pipeline stage the line describes;
* ``location`` - emitting module/function, so a line can be traced to code;
* ``version`` - kernel version that produced the data;
* ``level`` / ``message`` / timestamp;
* ``failure`` - present and structured *only* on failure lines, keeping
  failure reasons separate from normal progress;
* ``uncertainty`` - present when a conclusion is provisional (e.g. maximal
  flags on partial results are not yet finalized).
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def current_request_id() -> str | None:
    return _request_id.get()


def bind_request_id(request_id: str | None) -> str:
    rid = request_id or uuid.uuid4().hex
    _request_id.set(rid)
    return rid


def _reset_request_id() -> None:
    _request_id.set(None)


class JsonFormatter(logging.Formatter):
    def __init__(self, kernel_version: str):
        super().__init__()
        self._kernel_version = kernel_version

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "version": self._kernel_version,
        }
        rid = _request_id.get()
        if rid is not None:
            payload["request_id"] = rid
        for key in ("step", "location", "failure", "uncertainty", "context"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_logging(level: str, kernel_version: str) -> logging.Logger:
    logger = logging.getLogger("cfim")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(kernel_version))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_event(
    logger: logging.Logger,
    level: int,
    step: str,
    message: str,
    location: str,
    *,
    context: dict[str, Any] | None = None,
    failure: dict[str, Any] | None = None,
    uncertainty: str | None = None,
) -> None:
    extra: dict[str, Any] = {
        "step": step,
        "location": location,
        "request_id": _request_id.get(),
    }
    if context:
        extra["context"] = context
    if failure:
        extra["failure"] = failure
    if uncertainty:
        extra["uncertainty"] = uncertainty
    logger.log(level, message, extra=extra)
