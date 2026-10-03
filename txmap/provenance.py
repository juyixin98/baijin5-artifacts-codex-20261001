"""Provenance: every mapping decision is recorded with its request id.

Records carry a redacted view of inputs/outputs plus a content hash, so a
reviewer can verify *what* was decided and *why* without the log ever
holding raw sequence data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .models import IntervalOutcome, PointOutcome


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def redact(value: Any) -> Any:
    """Replace long strings (potential sequence data) with len+hash digests."""
    if isinstance(value, str):
        if len(value) > 24:
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            return f"<redacted len={len(value)} sha256:{digest}>"
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def point_outcome_dict(o: PointOutcome) -> dict:
    return {
        "status": o.status.value,
        "reason": o.reason.value,
        "transcript_id": o.transcript_id,
        "position": o.position,
        "mapped": o.mapped,
    }


def interval_outcome_dict(o: IntervalOutcome) -> dict:
    return {
        "status": o.status.value,
        "reason": o.reason.value,
        "transcript_id": o.transcript_id,
        "mapped_length": o.mapped_length,
        "fragments": [
            {
                "g_start": f.g_start,
                "g_end": f.g_end,
                "t_start": f.t_start,
                "t_end": f.t_end,
            }
            for f in o.fragments
        ],
        "gaps": [
            {"g_start": g.g_start, "g_end": g.g_end, "reason": g.reason.value}
            for g in o.gaps
        ],
    }


@dataclass(frozen=True)
class ProvenanceRecord:
    request_id: str
    endpoint: str
    transcript_id: str | None
    status: str
    reason: str
    input_redacted: dict
    output_redacted: dict
    input_sha256: str
    created_at: str

    @classmethod
    def build(
        cls,
        request_id: str,
        endpoint: str,
        transcript_id: str | None,
        status: str,
        reason: str,
        inputs: dict,
        outputs: dict,
    ) -> "ProvenanceRecord":
        return cls(
            request_id=request_id,
            endpoint=endpoint,
            transcript_id=transcript_id,
            status=status,
            reason=reason,
            input_redacted=redact(inputs),
            output_redacted=redact(outputs),
            input_sha256=content_hash(inputs),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_row(self) -> dict:
        row = asdict(self)
        row["input_redacted"] = _canonical(self.input_redacted)
        row["output_redacted"] = _canonical(self.output_redacted)
        return row
