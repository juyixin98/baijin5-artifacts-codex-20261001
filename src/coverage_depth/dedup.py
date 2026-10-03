"""Per-read overlap deduplication.

Blocks belonging to the same read_id — multiple CIGAR blocks of one record,
or both mates of a pair reported as separate records — are union-merged
before they reach the sweep. This makes paired-end overlap handling
EXPLICIT: an overlapping mate pair contributes depth 1, never 2, in the
overlap region. Distinct read_ids always count independently.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .models import Block, ReadBlock


def merge_intervals(blocks: Iterable[Block]) -> list[Block]:
    """Union-merge overlapping or touching half-open intervals.

    Touching intervals ([0,10) and [10,20)) merge into [0,20); this is a
    canonicalization only and never changes per-base coverage.
    """
    ordered = sorted(blocks, key=lambda b: (b.start, b.end))
    merged: list[Block] = []
    for block in ordered:
        if merged and block.start <= merged[-1].end:
            last = merged[-1]
            if block.end > last.end:
                merged[-1] = Block(last.start, block.end)
        else:
            merged.append(block)
    return merged


def merge_read_blocks(blocks: Iterable[ReadBlock]) -> list[ReadBlock]:
    """Union-merge blocks of ONE read on ONE reference."""
    blocks = list(blocks)
    if not blocks:
        return []
    read_id = blocks[0].read_id
    ref = blocks[0].ref
    merged = merge_intervals([Block(b.start, b.end) for b in blocks])
    return [ReadBlock(read_id, ref, b.start, b.end) for b in merged]


def group_and_merge(sorted_blocks: Iterable[ReadBlock]) -> Iterator[ReadBlock]:
    """Merge a stream of ReadBlocks pre-sorted by (read_id, ref, start).

    Emits one merged interval set per (read_id, ref) group, streaming.
    """
    current_key: tuple[str, str] | None = None
    current: list[ReadBlock] = []
    for block in sorted_blocks:
        key = (block.read_id, block.ref)
        if key != current_key:
            yield from merge_read_blocks(current)
            current_key = key
            current = []
        current.append(block)
    yield from merge_read_blocks(current)
