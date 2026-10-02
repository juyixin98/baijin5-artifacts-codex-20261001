"""Independent reference implementation of the documented flood semantics.

This module exists ONLY for the test-suite. It implements the same
documented contract as ``app.core.watershed`` (ascending elevation, flat
index tie-break, seed protection, ridge reclassification) with a different
data structure — per-level bucket heaps instead of one global heap — so the
kernel is cross-checked against code that does not share its implementation.

Hand-computed literal expectations in ``tests/fixtures.py`` remain the
primary reference for the small cases; this module covers the larger
fixtures where hand-tracing would be error-prone.
"""

from __future__ import annotations

import heapq
from typing import Optional

import numpy as np

from app.core.connectivity import iter_neighbors

WATERSHED_LINE = -1
UNLABELED = 0


def reference_flood(
    elevation: np.ndarray,
    markers: np.ndarray,
    connectivity: int = 8,
    mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Level-by-level bucket flood. Returns the label array only."""
    elev = np.asarray(elevation, dtype=np.float64)
    marks = np.asarray(markers)
    n_rows, n_cols = elev.shape
    in_mask = np.ones(elev.shape, dtype=bool) if mask is None else np.asarray(mask, dtype=bool)
    labels = np.where(in_mask, marks, UNLABELED).astype(np.int64)
    seeds = marks > 0

    buckets: dict[float, list[int]] = {}

    def push(level: float, idx: int) -> None:
        bucket = buckets.get(level)
        if bucket is None:
            bucket = []
            buckets[level] = bucket
        heapq.heappush(bucket, idx)

    for idx in np.flatnonzero(seeds & in_mask):
        push(float(elev.flat[idx]), int(idx))

    # One pixel at a time: always the smallest flat index in the lowest
    # non-empty level bucket. This reproduces the documented global
    # (elevation, flat index) order without sharing the kernel's heap.
    while buckets:
        level = min(buckets)
        bucket = buckets[level]
        idx = heapq.heappop(bucket)
        if not bucket:
            del buckets[level]
        r, c = divmod(idx, n_cols)
        p_label = int(labels[r, c])
        if p_label == WATERSHED_LINE:
            continue
        for qr, qc in iter_neighbors(r, c, n_rows, n_cols, connectivity):
            if not in_mask[qr, qc]:
                continue
            q_label = int(labels[qr, qc])
            if q_label == UNLABELED:
                labels[qr, qc] = p_label
                push(float(elev[qr, qc]), qr * n_cols + qc)
            elif q_label != p_label and q_label != WATERSHED_LINE:
                if not seeds[r, c]:
                    labels[r, c] = WATERSHED_LINE
                break
    return labels
