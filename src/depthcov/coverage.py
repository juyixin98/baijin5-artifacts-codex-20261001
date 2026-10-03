"""Coverage-depth engine: per-read union de-dup, sweep line, aggregates.

De-duplication policy is EXPLICIT, not accidental:

* ``union_per_query`` (default): all covered blocks carrying the same
  ``query_name`` are union-merged before depth is accumulated. One logical
  read (including two overlapping paired-end mates that share a QNAME)
  contributes at most +1 depth to any single base. Exact duplicate records
  for the same query are detected and rejected as ``duplicate``.
* ``per_record``: every accepted record contributes independently. Use this
  only when distinct records are known to be distinct molecules.

The sweep line emits maximal constant-depth segments. Two independent
weighted-length identities are computed and reconciled so a mismatch raises
rather than silently corrupting totals.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

from .models import (
    DepthResult,
    DepthSegment,
    Verdict,
)

#: Policy names.
UNION_PER_QUERY = "union_per_query"
PER_RECORD = "per_record"
VALID_POLICIES = frozenset({UNION_PER_QUERY, PER_RECORD})


def merge_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Union of half-open intervals. [0,5)+[5,10) merges to [0,10)."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[list[int]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _segments_from_events(
    events: dict[int, int], span: int
) -> tuple[tuple[DepthSegment, ...], int]:
    """Sweep aggregated ``{position: delta}`` events.

    Returns ``(segments, sweep_integral)`` where the integral is
    ``sum(length * depth)`` over every non-zero-depth segment.
    """
    segments: list[DepthSegment] = []
    depth = 0
    cur = 0
    integral = 0
    for pos in sorted(events):
        if pos > cur and depth > 0:
            segments.append(DepthSegment(cur, pos, depth))
            integral += (pos - cur) * depth
        depth += events[pos]
        cur = pos
        if depth < 0:
            raise AssertionError("sweep line depth went negative")
    if cur < span and depth > 0:
        segments.append(DepthSegment(cur, span, depth))
        integral += (span - cur) * depth
    if depth != 0:
        raise AssertionError(f"sweep line ended at depth {depth}, expected 0")
    return tuple(segments), integral


def coverage_for_reference(
    ref_name: str,
    ref_length: int,
    accepted: list[Verdict],
    *,
    dedup_policy: str = UNION_PER_QUERY,
) -> DepthResult:
    """Compute per-base depth, segments, histogram and conservation totals.

    ``accepted`` must all be accepted verdicts on ``ref_name``.
    """
    if dedup_policy not in VALID_POLICIES:
        raise ValueError(
            f"unknown dedup_policy {dedup_policy!r}; "
            f"expected one of {sorted(VALID_POLICIES)}"
        )
    if ref_length < 0:
        raise ValueError("ref_length must be >= 0")

    # Per-base via a difference array (deltas at block boundaries).
    delta = np.zeros(ref_length + 1, dtype=np.int64)
    events: dict[int, int] = defaultdict(int)
    block_total = 0  # sum of contributed covered bases, post union

    if dedup_policy == UNION_PER_QUERY:
        by_query: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for verdict in accepted:
            for block in verdict.blocks:
                by_query[verdict.query_name].append((block.start, block.end))
        contributed: list[tuple[str, tuple[tuple[int, int], ...]]] = []
        for query_name, intervals in by_query.items():
            union = merge_intervals(intervals)
            contributed.append((query_name, tuple(union)))
    else:
        contributed = [
            (
                verdict.query_name,
                tuple((b.start, b.end) for b in verdict.blocks),
            )
            for verdict in accepted
        ]

    for _query_name, intervals in contributed:
        for start, end in intervals:
            if not (0 <= start < end <= ref_length):
                raise ValueError(
                    f"block [{start},{end}) outside reference of "
                    f"length {ref_length}"
                )
            delta[start] += 1
            delta[end] -= 1
            events[start] += 1
            events[end] -= 1
            block_total += end - start

    per_base = np.cumsum(delta, dtype=np.int64)[:ref_length]

    # Histogram: histogram[d] = number of bases at exactly depth d.
    # Depth 0 is retained so sum(histogram.values()) == ref_length.
    counts = np.bincount(per_base, minlength=1)
    histogram: dict[int, int] = {}
    for depth_d, count in enumerate(counts):
        count = int(count)
        if count > 0 or depth_d == 0:
            histogram[int(depth_d)] = count

    segments, sweep_integral = _segments_from_events(events, ref_length)

    # Conservation: sweep integral, numpy integral and contributed block
    # total must all agree.
    numpy_integral = int(per_base.sum())
    if not (sweep_integral == numpy_integral == block_total):
        raise AssertionError(
            "weighted-length conservation violated: "
            f"sweep={sweep_integral} numpy={numpy_integral} "
            f"blocks={block_total}"
        )

    accepted_queries = tuple(sorted({q for q, _ in contributed}))
    return DepthResult(
        ref_name=ref_name,
        ref_length=ref_length,
        per_base_depth=per_base,
        segments=segments,
        histogram=histogram,
        weighted_length=block_total,
        covered_bases=int((per_base > 0).sum()),
        accepted_queries=accepted_queries,
    )
