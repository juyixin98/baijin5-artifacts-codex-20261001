"""Structured diagnostics with request/record correlation and redaction.

Every decision is rendered with a stable record/request id and the key state
that drove it. Free-text payloads (which may carry sensitive identifying
material in real deployments) are redacted: only a short, non-reversible
fingerprint plus the length is ever emitted.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Verdict

LOGGER_NAME = "depthcov"

#: Fields considered sensitive if they appear in diagnostic metadata.
SENSITIVE_KEYS = frozenset(
    {"seq", "sequence", "qual", "query_seq", "sample_id",
     "patient_id", "donor", "donor_id", "metadata", "tags"}
)


def redact(value: Any, *, keep: int = 4) -> str:
    """Return a non-reversible fingerprint for a potentially sensitive value."""
    text = "" if value is None else str(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    head = text[:keep] if text else ""
    return f"<redacted head={head!r} len={len(text)} sha256={digest}>"


def redact_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Copy a mapping, replacing values under sensitive keys with redaction."""
    safe: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in SENSITIVE_KEYS:
            safe[key] = redact(value)
        elif isinstance(value, dict):
            safe[key] = redact_mapping(value)
        else:
            safe[key] = value
    return safe


@dataclass
class DiagnosticEvent:
    request_id: str
    record_id: str
    outcome: str  # accepted | rejected | undetermined | error
    reason: str
    state: dict[str, Any]
    message: str
    elapsed_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "record_id": self.record_id,
            "outcome": self.outcome,
            "reason": self.reason,
            "state": redact_mapping(self.state),
            "message": self.message,
            "elapsed_ms": self.elapsed_ms,
        }


def configure_logging(level: int = logging.INFO, *, log_file: str | None = None):
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.handlers.clear()
    handler: logging.Handler = logging.StreamHandler()
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def verdict_event(
    verdict: Verdict,
    *,
    request_id: str,
    elapsed_ms: float | None = None,
) -> DiagnosticEvent:
    """Build a diagnostic event explaining why a record was accepted/rejected."""
    if verdict.accepted:
        outcome, message = "accepted", f"record contributes coverage ({verdict.detail})"
    elif verdict.reason == "undetermined":
        outcome, message = "undetermined", verdict.detail
    else:
        outcome, message = "rejected", f"record excluded: {verdict.detail}"

    state = {
        "ref": verdict.ref_name,
        "ref_start": verdict.ref_start,
        "ref_end": verdict.ref_end,
        "mapq": verdict.mapq,
        "block_count": len(verdict.blocks),
        "covered_bases": sum(b.length for b in verdict.blocks),
    }
    return DiagnosticEvent(
        request_id=request_id,
        record_id=verdict.query_name,
        outcome=outcome,
        reason=verdict.reason,
        state=state,
        message=message,
        elapsed_ms=elapsed_ms,
    )


def malformed_event(
    raw: str,
    error: Exception,
    *,
    request_id: str,
    record_index: int,
) -> DiagnosticEvent:
    """A record that could not even be parsed -> undetermined, not silently dropped."""
    return DiagnosticEvent(
        request_id=request_id,
        record_id=f"row#{record_index}",
        outcome="undetermined",
        reason="malformed_record",
        state={"raw": redact(raw), "error_type": type(error).__name__},
        message=f"record could not be parsed or judged: {error}",
    )


class Timer:
    """Small monotonic timer for per-record elapsed_ms."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000.0, 3)
