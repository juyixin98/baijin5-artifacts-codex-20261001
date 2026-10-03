"""Pipeline orchestration.

Stages per run:
  1. parse      fixture lines / API payloads into AlignmentRecords
  2. filter     quality decisions (accept / reject / undecidable)
  3. expand     CIGAR -> covered blocks; gap ops contribute nothing
  4. bounds     blocks outside [0, ref_length) are rejected
  5. dedup      external sort by read_id, union-merge per read
  6. sweep      external sort by position, sweep-line segmentation
  7. conserve   verify weighted-length conservation before persisting
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable

from .config import PipelineConfig
from .dedup import group_and_merge
from .diagnostics import get_logger, log_decision
from .errors import DecisionStatus, ReasonCode
from .externalsort import external_sort
from .filtering import evaluate_record
from .models import (
    AlignmentRecord,
    Block,
    CoverageResult,
    Decision,
    ReadBlock,
)
from .parsing import CigarError, cigar_to_blocks
from .sweep import sweep_segments


class ConservationError(RuntimeError):
    """Weighted-length conservation check failed; result must not ship."""


def _block_to_jsonable(block: ReadBlock) -> list:
    return [block.read_id, block.ref, block.start, block.end]


def _block_from_jsonable(raw: list) -> ReadBlock:
    return ReadBlock(raw[0], raw[1], int(raw[2]), int(raw[3]))


def run_pipeline(
    records: Iterable[AlignmentRecord],
    ref_name: str,
    ref_length: int,
    config: PipelineConfig,
    spill_dir: str | None = None,
    logger: logging.LoggerAdapter | None = None,
) -> CoverageResult:
    """Execute all stages and return the coverage result with decisions."""
    log = logger or get_logger("coverage_depth.pipeline")
    decisions: list[Decision] = []
    accepted_blocks: list[ReadBlock] = []

    for record in records:
        decision = evaluate_record(record, config.filter)
        if decision.status is not DecisionStatus.ACCEPTED:
            decisions.append(decision)
            log_decision(log, decision)
            continue
        try:
            blocks = cigar_to_blocks(record.cigar, record.start)
        except CigarError as exc:
            decision = Decision(
                record.record_index, record.read_id,
                DecisionStatus.REJECTED, ReasonCode.CIGAR_ERROR, str(exc),
            )
            decisions.append(decision)
            log_decision(log, decision)
            continue
        if not blocks:
            decision = Decision(
                record.record_index, record.read_id,
                DecisionStatus.REJECTED, ReasonCode.EMPTY_INTERVAL,
                "CIGAR produced no covered reference bases",
            )
            decisions.append(decision)
            log_decision(log, decision)
            continue
        if blocks[-1].end > ref_length:
            decision = Decision(
                record.record_index, record.read_id,
                DecisionStatus.REJECTED, ReasonCode.OUT_OF_BOUNDS,
                f"block end {blocks[-1].end} exceeds reference length {ref_length}",
            )
            decisions.append(decision)
            log_decision(log, decision)
            continue
        decisions.append(decision)
        log_decision(log, decision, level=logging.DEBUG)
        accepted_blocks.extend(
            ReadBlock(record.read_id, ref_name, b.start, b.end) for b in blocks
        )

    # Stage 5: sort by read_id so every block of a read (both mates, split
    # CIGAR blocks) lands in one group, then union-merge per read.
    by_read = external_sort(
        accepted_blocks,
        key=lambda b: (b.read_id, b.ref, b.start, b.end),
        chunk_size=config.sort_chunk_size,
        spill_dir=spill_dir,
        to_jsonable=_block_to_jsonable,
        from_jsonable=_block_from_jsonable,
    )
    merged = group_and_merge(by_read)

    # Stage 6: sort merged blocks by position and sweep.
    by_position = external_sort(
        merged,
        key=lambda b: (b.ref, b.start, b.end),
        chunk_size=config.sort_chunk_size,
        spill_dir=spill_dir,
        to_jsonable=_block_to_jsonable,
        from_jsonable=_block_from_jsonable,
    )
    merged_blocks = list(by_position)
    segments, histogram, covered, weighted = sweep_segments(merged_blocks, ref_length)

    # Stage 7: conservation — the sweep must neither create nor destroy
    # covered bases relative to the deduplicated input blocks.
    expected = sum(b.end - b.start for b in merged_blocks)
    if weighted != expected:
        raise ConservationError(
            f"weighted length {weighted} != deduplicated block bases {expected}"
        )

    result = CoverageResult(
        ref=ref_name,
        ref_length=ref_length,
        segments=segments,
        histogram=histogram,
        covered_bases=covered,
        weighted_bases=weighted,
        mean_depth=weighted / ref_length if ref_length else 0.0,
        decisions=tuple(decisions),
    )
    log.info(
        "pipeline completed",
        extra={
            "context": {
                "ref": ref_name,
                "ref_length": ref_length,
                "records": len(decisions),
                "accepted": sum(
                    1 for d in decisions if d.status is DecisionStatus.ACCEPTED
                ),
                "covered_bases": covered,
                "weighted_bases": weighted,
                "mean_depth": round(result.mean_depth, 6),
            }
        },
    )
    return result


def input_digest(records: list[AlignmentRecord], ref_name: str, ref_length: int) -> str:
    """Stable digest of the exact input a run consumed, for provenance."""
    hasher = hashlib.sha256()
    hasher.update(json.dumps([ref_name, ref_length]).encode())
    for r in records:
        hasher.update(
            json.dumps([r.record_index, r.read_id, r.ref, r.start,
                        r.mapq, r.flags, r.cigar]).encode()
        )
    return hasher.hexdigest()
