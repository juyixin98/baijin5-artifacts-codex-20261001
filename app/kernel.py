"""Topology-preserving parallel thinning kernel (Zhang-Suen, 1984).

Adjacency contract
------------------
Foreground pixels use 8-connectivity, background pixels use 4-connectivity.
This is a compatible adjacency pair: it keeps the digital Jordan curve
property, so "connected component" and "hole" are well defined on both
sides of the foreground/background boundary.

Deletion contract
-----------------
One *round* consists of two sub-iterations. Within each sub-iteration the
deletion mask is computed purely from the SAME prior state and applied
simultaneously (parallel deletion); no pixel sees the result of another
pixel's deletion inside the same sub-iteration. Sub-iteration 2 reads the
state produced by sub-iteration 1. This is what keeps the result
independent of scan order and makes tiled execution exact.

Preservation contract
---------------------
- Endpoints (pixels with exactly one foreground neighbour) are never
  deleted: the condition 2 <= B(P) <= 6 excludes them.
- Holes are preserved: the A(P) == 1 (single 0->1 transition) condition
  forbids deleting a pixel whose removal would merge a hole with the
  outside background or split a component.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Clockwise neighbour order starting at north: P2, P3, ..., P9.
_OFFSETS = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def _neighbour_planes(image: np.ndarray) -> list[np.ndarray]:
    """Return the 8 neighbour planes P2..P9 aligned with ``image``."""
    padded = np.pad(image, 1, mode="constant")
    height, width = image.shape
    return [
        padded[1 + dr : 1 + dr + height, 1 + dc : 1 + dc + width]
        for dr, dc in _OFFSETS
    ]


def deletion_mask(image: np.ndarray, subiteration: int) -> np.ndarray:
    """Boolean mask of foreground pixels deletable in this sub-iteration.

    The mask depends only on ``image`` (the shared prior state); callers
    must apply it simultaneously, never pixel-by-pixel in place.
    """
    if subiteration not in (1, 2):
        raise ValueError(f"subiteration must be 1 or 2, got {subiteration}")
    img = np.ascontiguousarray(image).astype(np.uint8)
    p = _neighbour_planes(img)
    p2, p3, p4, p5, p6, p7, p8, p9 = p

    # B(P): number of foreground neighbours.
    b = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
    # A(P): number of 0->1 transitions in the cyclic sequence P2..P9,P2.
    a = np.zeros(img.shape, dtype=np.uint8)
    for i in range(8):
        a += (1 - p[i]) * p[(i + 1) % 8]

    mask = (img == 1) & (b >= 2) & (b <= 6) & (a == 1)
    if subiteration == 1:
        mask &= (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
    else:
        mask &= (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
    return mask


@dataclass(frozen=True)
class ThinningResult:
    skeleton: np.ndarray  # uint8 0/1
    rounds: int
    deletions_per_round: tuple[tuple[int, int], ...]  # (sub1, sub2) per round
    converged: bool

    @property
    def total_deleted(self) -> int:
        return sum(a + b for a, b in self.deletions_per_round)


def thin(image: np.ndarray, max_rounds: int = 256) -> ThinningResult:
    """Thin a binary image to its skeleton. Pure function of the input."""
    current = np.ascontiguousarray(image).astype(np.uint8).copy()
    deletions: list[tuple[int, int]] = []
    converged = False
    for _ in range(max_rounds):
        mask1 = deletion_mask(current, subiteration=1)
        current = current & ~mask1  # simultaneous apply from same prior state
        mask2 = deletion_mask(current, subiteration=2)
        current = current & ~mask2
        pair = (int(mask1.sum()), int(mask2.sum()))
        deletions.append(pair)
        if pair == (0, 0):
            converged = True
            break
    return ThinningResult(
        skeleton=current.astype(np.uint8),
        rounds=len(deletions),
        deletions_per_round=tuple(deletions),
        converged=converged,
    )
