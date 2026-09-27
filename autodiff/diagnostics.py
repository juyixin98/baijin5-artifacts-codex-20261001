"""Structured diagnostics with request/record identity and redaction.

Every acceptance/rejection/undetermined decision goes through
:class:`Diagnostics` so each record carries:

* ``record_id`` / ``request_id``  - correlation identifiers
* ``event``                       - what happened (accept/reject/unable/...)
* ``status``                      - ACCEPTED / REJECTED / UNABLE
* ``reason``                      - why
* ``state``                       - small, redacted snapshot of key state

Array payloads are summarised (never printed in full), satisfying the
"sensitive data is redacted" requirement uniformly.
"""
from __future__ import annotations

import itertools
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np

from .config import DIAG_MAX_ELEMENTS, Config

_record_counter = itertools.count(1)


def new_record_id() -> str:
    return f"rec-{next(_record_counter):06d}"


# Status vocabulary used by verifiers and the version checker.
ACCEPTED = "ACCEPTED"
REJECTED = "REJECTED"
UNABLE = "UNABLE"

_VALID_STATUSES = {ACCEPTED, REJECTED, UNABLE}


def redact_array(a: np.ndarray, max_elements: int = DIAG_MAX_ELEMENTS) -> dict[str, Any]:
    """Return a safe summary of an array: shape/dtype + a truncated preview.

    Values themselves are only shown for very small arrays; for larger ones we
    expose min/max/finite counts instead, so large or sensitive payloads are
    not leaked into logs.
    """
    a = np.asarray(a)
    summary: dict[str, Any] = {
        "shape": list(a.shape),
        "dtype": str(a.dtype),
        "size": int(a.size),
    }
    if a.size == 0:
        summary["preview"] = []
        return summary
    finite = np.isfinite(a)
    summary["finite_count"] = int(finite.sum())
    summary["nonfinite_count"] = int(a.size - finite.sum())
    if a.size <= max_elements:
        summary["preview"] = np.round(a.astype(np.float64), 6).tolist()
    else:
        flat = a.reshape(-1)
        summary["head"] = np.round(flat[:max_elements].astype(np.float64), 6).tolist()
        summary["min"] = float(np.nanmin(a)) if finite.any() else None
        summary["max"] = float(np.nanmax(a)) if finite.any() else None
    return summary


def redact(value: Any, max_elements: int = DIAG_MAX_ELEMENTS) -> Any:
    """Redact a single value for safe logging."""
    if isinstance(value, np.ndarray):
        return redact_array(value, max_elements)
    if isinstance(value, (str, bytes)):
        # Treat long scalar strings as potentially sensitive.
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        if len(text) > 32:
            return f"<redacted str len={len(text)}>"
        return text
    if isinstance(value, dict):
        return {str(k): redact(v, max_elements) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        shown = [redact(v, max_elements) for v in list(value)[:max_elements]]
        if len(value) > max_elements:
            shown.append(f"...<{len(value) - max_elements} more>")
        return shown
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


@dataclass
class DiagnosticRecord:
    record_id: str
    request_id: str
    event: str
    status: str
    reason: str
    state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "request_id": self.request_id,
            "event": self.event,
            "status": self.status,
            "reason": self.reason,
            "state": redact(self.state),
        }


class Diagnostics:
    """Collects and optionally emits diagnostic records.

    A single instance is safe to share across a request (and thread-safe for
    collection).  ``request_id`` groups all records produced while handling one
    verification request.
    """

    def __init__(
        self,
        request_id: str,
        config: Optional[Config] = None,
        sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.request_id = request_id
        self._config = config
        self._sink = sink
        self._lock = threading.Lock()
        self.records: list[DiagnosticRecord] = []

    def emit(
        self,
        event: str,
        status: str,
        reason: str,
        **state: Any,
    ) -> DiagnosticRecord:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid diagnostic status {status!r}")
        record = DiagnosticRecord(
            record_id=new_record_id(),
            request_id=self.request_id,
            event=event,
            status=status,
            reason=reason,
            state=state,
        )
        with self._lock:
            self.records.append(record)
        if self._sink is not None:
            self._sink(record.to_dict())
        elif self._config is None or self._config.log_diagnostics:
            print(f"[{record.record_id}/{record.request_id}] "
                  f"{event} {status}: {reason}", file=sys.stderr)
        return record

    def accepted(self, event: str, reason: str, **state: Any) -> DiagnosticRecord:
        return self.emit(event, ACCEPTED, reason, **state)

    def rejected(self, event: str, reason: str, **state: Any) -> DiagnosticRecord:
        return self.emit(event, REJECTED, reason, **state)

    def unable(self, event: str, reason: str, **state: Any) -> DiagnosticRecord:
        return self.emit(event, UNABLE, reason, **state)

    def status_counts(self) -> dict[str, int]:
        counts = {ACCEPTED: 0, REJECTED: 0, UNABLE: 0}
        with self._lock:
            for r in self.records:
                counts[r.status] += 1
        return counts
