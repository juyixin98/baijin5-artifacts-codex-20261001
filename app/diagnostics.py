"""Request-scoped diagnostics: request ids, decision logging, redaction.

Every log line carries a ``request_id`` (from the ``X-Request-ID`` header or
generated).  Decisions are logged as ``accepted`` / ``rejected`` /
``undecidable`` with a human-readable reason and the key state that drove
the decision.  Sample payloads are sensitive-adjacent (potentially large and
meaningless in logs): only their length and a truncated SHA-1 digest are
ever logged, never the samples themselves.
"""
from __future__ import annotations

import contextvars
import hashlib
import logging
import uuid
from typing import Iterable

import numpy as np

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def set_request_id(value: str) -> contextvars.Token:
    return _request_id.set(value)


def reset_request_id(token: contextvars.Token) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def sample_digest(samples: Iterable[float]) -> str:
    """Redacted fingerprint of a sample payload: length + truncated digest."""
    arr = np.asarray(list(samples) if not isinstance(samples, np.ndarray) else samples)
    digest = hashlib.sha1(arr.tobytes()).hexdigest()[:12]
    return f"n={arr.size} sha1={digest}"


def log_decision(
    logger: logging.Logger,
    decision: str,
    reason: str,
    **key_state: object,
) -> None:
    """Emit one structured decision line. ``decision`` is accepted/rejected/undecidable."""
    state = " ".join(f"{k}={v}" for k, v in sorted(key_state.items()))
    logger.info("decision=%s reason=%s%s", decision, reason, f" {state}" if state else "")
