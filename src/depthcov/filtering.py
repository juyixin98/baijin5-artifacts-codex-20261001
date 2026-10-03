"""Quality filtering and per-alignment adjudication.

A filter turns an alignment into a :class:`Verdict`. ``ACCEPT`` verdicts carry
the gap-free covered blocks; every other verdict explains precisely why the
record was rejected or could not be judged.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cigar import CigarError, blocks_for_alignment
from .models import Alignment, RejectReason, Verdict


@dataclass(frozen=True)
class FilterConfig:
    """Quality thresholds. ``min_mapq`` is inclusive."""

    min_mapq: int = 20
    # When True, an exact duplicate (same query + blocks) is counted once.
    dedupe_overlapping_reads: bool = True
    # Reject records explicitly flagged as PCR/optical duplicates.
    reject_flagged_duplicates: bool = True


def adjudicate(
    alignment: Alignment,
    config: FilterConfig,
    ref_length: int | None,
    *,
    known_refs: set[str] | None = None,
) -> Verdict:
    """Decide one alignment against coordinates and quality thresholds.

    Order of checks is fixed and reflected in the verdict reason:

    1. reference known (if a registry is supplied)
    2. MAPQ threshold
    3. CIGAR validity
    4. interval / bounds validity
    """
    base = dict(
        query_name=alignment.query_name,
        ref_name=alignment.ref_name,
        ref_start=alignment.ref_start,
        mapq=alignment.mapq,
    )

    if known_refs is not None and alignment.ref_name not in known_refs:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.UNKNOWN_REFERENCE.value,
            detail=(
                f"reference {alignment.ref_name!r} is not in the reference "
                f"registry {sorted(known_refs)}"
            ),
            ref_end=None,
        )

    if config.reject_flagged_duplicates and alignment.is_duplicate:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.DUPLICATE.value,
            detail="record is flagged is_duplicate=True (SAM FLAG 0x400)",
            ref_end=None,
        )

    if alignment.mapq < config.min_mapq:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.LOW_MAPQ.value,
            detail=(
                f"MAPQ {alignment.mapq} below minimum {config.min_mapq}"
            ),
            ref_end=None,
        )

    try:
        blocks, ref_end = blocks_for_alignment(alignment)
    except CigarError as exc:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.INVALID_CIGAR.value,
            detail=f"malformed CIGAR {alignment.cigar!r}: {exc}",
            ref_end=None,
        )

    if ref_end < alignment.ref_start:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.INVALID_INTERVAL.value,
            detail=(
                f"derived ref_end {ref_end} < ref_start {alignment.ref_start}"
            ),
            ref_end=ref_end,
        )

    if ref_length is not None and ref_end > ref_length:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.OUT_OF_BOUNDS.value,
            detail=(
                f"derived footprint [{alignment.ref_start},{ref_end}) exceeds "
                f"reference length {ref_length}"
            ),
            ref_end=ref_end,
        )

    if not blocks:
        return Verdict(
            **base,
            accepted=False,
            reason=RejectReason.NO_COVERED_BASES.value,
            detail="CIGAR contains no M/=/X bases (gap/clip only)",
            ref_end=ref_end,
        )

    return Verdict(
        **base,
        accepted=True,
        reason="accepted",
        detail=(
            f"{len(blocks)} covered block(s) over "
            f"[{alignment.ref_start},{ref_end}), "
            f"{sum(b.length for b in blocks)} covered base(s) after gaps"
        ),
        ref_end=ref_end,
        blocks=tuple(blocks),
    )
