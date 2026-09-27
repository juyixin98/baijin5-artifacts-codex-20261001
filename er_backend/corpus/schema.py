"""Corpus-level validation: the contract every ingested batch must satisfy."""

from __future__ import annotations

from ..errors import InputValidationError, ResourceExhaustedError
from ..models import Record


def validate_batch(records: list[Record], *, max_records: int) -> None:
    """Validate an ingest batch as a whole (per-record shape is enforced by
    the Record model itself)."""
    if len(records) > max_records:
        raise ResourceExhaustedError(
            f"batch of {len(records)} records exceeds max_records={max_records}",
            details={"size": len(records), "max_records": max_records},
        )
    seen: set[str] = set()
    duplicates: list[str] = []
    for rec in records:
        if rec.record_id in seen:
            duplicates.append(rec.record_id)
        seen.add(rec.record_id)
    if duplicates:
        raise InputValidationError(
            "duplicate record_id(s) in batch",
            details={"duplicates": sorted(set(duplicates))},
        )


def require_known_ids(ids: list[str], known: set[str], *, context: str) -> None:
    unknown = sorted(set(ids) - known)
    if unknown:
        raise InputValidationError(
            f"{context} references unknown record id(s)",
            details={"unknown_ids": unknown, "context": context},
        )
