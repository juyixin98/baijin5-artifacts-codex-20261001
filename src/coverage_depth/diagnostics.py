"""Structured diagnostics.

Every log line carries a request id (one per API call / pipeline run) and,
for record-level events, the record index plus a MASKED read id. Raw read
names are treated as potentially identifying sample metadata and are never
logged in clear.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "coverage_request_id", default="-"
)


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def get_request_id() -> str:
    return _request_id.get()


def mask_read_id(read_id: str) -> str:
    """One-way mask for read names in logs: prefix + short digest."""
    if not read_id:
        return "<empty>"
    digest = hashlib.sha256(read_id.encode("utf-8")).hexdigest()[:8]
    return f"{read_id[0]}***{digest}"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", get_request_id()),
            "msg": record.getMessage(),
        }
        for key, value in getattr(record, "context", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def get_logger(name: str) -> logging.LoggerAdapter:
    """Logger adapter that injects the current request id into every record."""
    base = logging.getLogger(name)
    if not base.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        base.addHandler(handler)
        base.setLevel(logging.INFO)
        base.propagate = False
    return logging.LoggerAdapter(base, {"request_id": get_request_id()})


def log_decision(logger: logging.LoggerAdapter, decision, level: int = logging.INFO) -> None:
    """Emit one structured line explaining why a record was accepted/rejected."""
    logger.log(
        level,
        "record decision",
        extra={
            "context": {
                "record_index": decision.record_index,
                "read_id_masked": mask_read_id(decision.read_id),
                "status": decision.status.value,
                "reason": decision.reason.value,
                "detail": decision.detail,
            }
        },
    )
