"""Diagnostics: decision records with request ids and masked payloads.

Raw feature values are treated as sensitive: diagnostics and logs only ever
carry sequence *lengths* and a truncated content hash, never the values
themselves. Every alignment attempt — accepted, rejected, or indeterminate —
produces exactly one ``DecisionRecord`` stating why.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from dtw_service.contracts import DecisionStatus

logger = logging.getLogger("dtw_service.diagnostics")


def mask_sequence(seq: Sequence[float]) -> str:
    """Sensitive-data-safe fingerprint: length + truncated SHA-256, no values."""
    digest = hashlib.sha256(
        ",".join(repr(float(x)) for x in seq).encode("utf-8")
    ).hexdigest()
    return f"len={len(seq)} sha256={digest[:12]}"


@dataclass(frozen=True)
class DecisionRecord:
    """One auditable decision: what was decided, and why."""

    request_id: str
    status: DecisionStatus
    reason: str
    key_state: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status.value,
            "reason": self.reason,
            **self.key_state,
        }


def log_decision(record: DecisionRecord) -> None:
    """Emit the decision record; payloads are already masked by construction."""
    log = logger.info if record.status is DecisionStatus.ACCEPTED else logger.warning
    log("dtw decision %s", record.as_dict())
