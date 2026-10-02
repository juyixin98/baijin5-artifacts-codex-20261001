"""FIFO-queue kernel (Vincent's hybrid reconstruction, queue phase).

Instead of re-dilating the whole image every round, this engine pushes
each pixel's current upper bound to its neighbors exactly when that
bound improves.  For the monotone operator min(dilate(.), mask) on a
finite lattice, the queue schedule reaches the same least fixpoint as
synchronous iteration (Vincent 1993); the test-suite asserts this
equivalence against the reference kernel on every fixture.

Boundary rule: neighbors are enumerated with explicit in-bounds checks,
so border pixels have fewer neighbors — identical to the reference's
``mode="constant", cval=0`` for non-negative dilation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

from ..schemas import Connectivity

# Precomputed neighbor offsets per connectivity (fixed neighborhood rule).
_OFFSETS = {
    Connectivity.FOUR: ((-1, 0), (1, 0), (0, -1), (0, 1)),
    Connectivity.EIGHT: (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ),
}


@dataclass
class KernelStats:
    """Convergence record shared by all engines.

    iterations: synchronous sweeps (reference/tiled); None for queue.
    queue_pops: pixels dequeued (queue engine); None otherwise.
    changed_pixels: pixels updated in the final pass / total updates.
    """

    iterations: int | None = None
    queue_pops: int | None = None
    changed_pixels: int | None = None


def reconstruct_queue(
    marker: np.ndarray,
    mask: np.ndarray,
    connectivity: Connectivity = Connectivity.EIGHT,
) -> tuple[np.ndarray, KernelStats]:
    """Reconstruct marker under mask by dilation using a FIFO queue.

    Returns (result, stats); stats.queue_pops counts dequeue operations
    and stats.changed_pixels counts pixel value updates.
    """
    if marker.shape != mask.shape:
        raise ValueError("marker and mask must have the same shape")

    rec = marker.copy()
    rows, cols = rec.shape
    offsets = _OFFSETS[connectivity]

    def neighbors(r: int, c: int):
        for dr, dc in offsets:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                yield nr, nc

    # Initialization: a pixel can only propagate if some neighbor is
    # strictly below it and still below its own mask ceiling.
    queue: deque[tuple[int, int]] = deque()
    for r in range(rows):
        for c in range(cols):
            value = rec[r, c]
            if value == 0:
                continue
            for nr, nc in neighbors(r, c):
                if rec[nr, nc] < value and rec[nr, nc] < mask[nr, nc]:
                    queue.append((r, c))
                    break

    pops = 0
    updates = 0
    while queue:
        r, c = queue.popleft()
        pops += 1
        value = rec[r, c]
        for nr, nc in neighbors(r, c):
            if rec[nr, nc] < value and rec[nr, nc] < mask[nr, nc]:
                new_value = min(value, mask[nr, nc])
                if new_value > rec[nr, nc]:
                    rec[nr, nc] = new_value
                    updates += 1
                    queue.append((nr, nc))

    return rec, KernelStats(queue_pops=pops, changed_pixels=updates)
