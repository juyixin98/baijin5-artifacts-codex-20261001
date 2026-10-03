"""Request-scoped diagnostic logging.

Every accept / reject / undecidable verdict is logged with a request id and
the key numeric state that explains it. Sample data itself is never logged;
caller-supplied labels are reduced to a truncated SHA-256 digest so logs stay
useful without carrying potentially sensitive identifiers.
"""
from __future__ import annotations

import hashlib
import logging
import uuid

from .contracts import Decision

logger = logging.getLogger("wsola_backend")


def new_request_id() -> str:
    return uuid.uuid4().hex


def redact_label(label: str | None) -> str | None:
    if label is None:
        return None
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]


def log_decision(
    request_id: str,
    decision: Decision,
    reason: str,
    *,
    request_log: logging.Logger | None = None,
    **state: object,
) -> None:
    """Emit one structured line explaining why a request was accepted/rejected/undecidable."""
    log = request_log or logger
    fields = " ".join(f"{k}={v!r}" for k, v in sorted(state.items()))
    log.info("request_id=%s decision=%s reason=%s %s", request_id, decision.value, reason, fields)
