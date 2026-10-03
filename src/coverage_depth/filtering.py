"""Quality filtering: turn each AlignmentRecord into an explicit Decision.

Every record gets exactly one decision with a stable reason code, so the
audit trail can answer "why was this record accepted / rejected / left
undecidable" without re-running anything.
"""

from __future__ import annotations

from .config import (
    FLAG_DUPLICATE,
    FLAG_QC_FAIL,
    FLAG_SECONDARY,
    FLAG_SUPPLEMENTARY,
    FLAG_UNMAPPED,
    FilterConfig,
)
from .errors import DecisionStatus, ReasonCode
from .models import AlignmentRecord, Decision


def evaluate_record(record: AlignmentRecord, cfg: FilterConfig) -> Decision:
    """Classify one record. Pure function of (record, config)."""

    def decide(status: DecisionStatus, reason: ReasonCode, detail: str) -> Decision:
        return Decision(record.record_index, record.read_id, status, reason, detail)

    if record.flags & FLAG_UNMAPPED:
        return decide(DecisionStatus.REJECTED, ReasonCode.UNMAPPED, "flag 0x4 set")
    if cfg.exclude_secondary and record.flags & FLAG_SECONDARY:
        return decide(DecisionStatus.REJECTED, ReasonCode.SECONDARY, "flag 0x100 set")
    if cfg.exclude_supplementary and record.flags & FLAG_SUPPLEMENTARY:
        return decide(DecisionStatus.REJECTED, ReasonCode.SUPPLEMENTARY, "flag 0x800 set")
    if cfg.exclude_qc_fail and record.flags & FLAG_QC_FAIL:
        return decide(DecisionStatus.REJECTED, ReasonCode.QC_FAIL, "flag 0x200 set")
    if cfg.exclude_duplicates and record.flags & FLAG_DUPLICATE:
        return decide(DecisionStatus.REJECTED, ReasonCode.DUPLICATE, "flag 0x400 set")
    if record.mapq is None:
        # Quality is unknown, not known-bad: we cannot decide, so the record
        # is excluded from coverage but reported separately from rejects.
        return decide(
            DecisionStatus.UNDECIDABLE,
            ReasonCode.MAPQ_UNKNOWN,
            "mapq missing; cannot evaluate quality threshold",
        )
    if record.mapq < cfg.min_mapq:
        return decide(
            DecisionStatus.REJECTED,
            ReasonCode.LOW_MAPQ,
            f"mapq {record.mapq} < min_mapq {cfg.min_mapq}",
        )
    return decide(
        DecisionStatus.ACCEPTED,
        ReasonCode.ACCEPTED,
        f"mapq {record.mapq} >= min_mapq {cfg.min_mapq}; flags 0x{record.flags:x} clean",
    )
