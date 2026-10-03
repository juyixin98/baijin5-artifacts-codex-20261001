"""Sweep-line segmentation over merged read blocks (NumPy-vectorized).

Events are +1 at each block start and -1 at each block end. Because
intervals are half-open, all events at one position are applied as a net
delta before the depth of the following segment is read off — so a block
ending at p and a block starting at p never overlap at p.

Conservation invariant (checked by callers and tests):
    sum(depth * length over segments) == sum(length of input blocks)
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .models import ReadBlock, Segment


def sweep_segments(
    blocks: Iterable[ReadBlock], ref_length: int
) -> tuple[tuple[Segment, ...], dict[int, int], int, int]:
    """Segment one reference into constant-depth intervals.

    Returns (segments, histogram, covered_bases, weighted_bases). Segments
    carry only covered (depth >= 1) intervals; the histogram maps depth ->
    number of reference bases at that depth and includes depth 0 for the
    uncovered remainder of the reference.
    """
    starts: list[int] = []
    ends: list[int] = []
    for block in blocks:
        starts.append(block.start)
        ends.append(block.end)

    if not starts:
        return (), {0: ref_length}, 0, 0

    positions = np.array(starts + ends, dtype=np.int64)
    deltas = np.concatenate(
        [np.ones(len(starts), dtype=np.int64), -np.ones(len(ends), dtype=np.int64)]
    )
    order = np.argsort(positions, kind="stable")
    positions, deltas = positions[order], deltas[order]

    # Net delta per unique event position.
    unique_pos, first_idx = np.unique(positions, return_index=True)
    net = np.add.reduceat(deltas, first_idx)

    # Depth of the segment starting at unique_pos[i] is the running depth
    # AFTER applying the net delta at that position (half-open semantics).
    depths = np.cumsum(net)
    boundaries = np.append(unique_pos, ref_length)
    lengths = np.diff(boundaries)

    # Zero-depth regions are reported through the histogram only; the
    # segment list carries covered intervals.
    keep = (lengths > 0) & (depths > 0)
    seg_starts = boundaries[:-1][keep]
    seg_ends = boundaries[1:][keep]
    seg_depths = depths[keep]
    seg_lengths = lengths[keep]

    segments = tuple(
        Segment(int(s), int(e), int(d))
        for s, e, d in zip(seg_starts, seg_ends, seg_depths)
    )

    # Depth histogram over the whole reference, including uncovered bases.
    histogram_array = np.bincount(
        seg_depths, weights=seg_lengths.astype(np.float64), minlength=1
    )
    histogram = {
        int(depth): int(round(bases))
        for depth, bases in enumerate(histogram_array)
        if bases > 0
    }
    accounted = sum(histogram.values())
    if accounted < ref_length:  # leading/trailing zero-depth regions
        histogram[0] = histogram.get(0, 0) + (ref_length - accounted)

    covered = int(sum(length for depth, length in histogram.items() if depth > 0))
    weighted = int(sum(depth * length for depth, length in histogram.items()))
    return segments, histogram, covered, weighted
