"""Independent brute-force reference for the Euclidean distance transform.

Deliberately *not* implemented with the separable-envelope kernel: this
module exists so tests can check the optimized kernel against an independent
specification written straight from the mathematical definition. Every
target pixel is compared against every source pixel in an explicit double
loop, so this is O(H*W*S) and suitable only for small fixtures.

Tie rule under exact equality (``math.isclose`` in squared distance): choose
the lexicographically smallest source coordinate ``(row, column)`` — the
same contract the kernel implements, expressed independently.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ReferenceEDT:
    distances: list[list[float]]
    nearest_y: list[list[int]]
    nearest_x: list[list[int]]


def brute_force_edt(
    mask: np.ndarray,
    spacing_y: float = 1.0,
    spacing_x: float = 1.0,
) -> ReferenceEDT:
    m = np.asarray(mask, dtype=bool)
    h, w = m.shape
    sources = [(int(y), int(x)) for y in range(h) for x in range(w) if m[y, x]]

    diag2 = spacing_y * spacing_y + spacing_x * spacing_x
    abs_floor = 1e-12 * max(diag2, 1.0)

    dist: list[list[float]] = [[math.inf] * w for _ in range(h)]
    ny: list[list[int]] = [[-1] * w for _ in range(h)]
    nx: list[list[int]] = [[-1] * w for _ in range(h)]

    for ty in range(h):
        for tx in range(w):
            if not sources:
                continue
            # A source pixel maps to itself; this also keeps dense fixtures
            # from degenerating to O(H*W*S) Python iterations.
            if m[ty, tx]:
                dist[ty][tx] = 0.0
                ny[ty][tx] = ty
                nx[ty][tx] = tx
                continue
            best_d2 = math.inf
            best_sy, best_sx = -1, -1
            for sy_, sx_ in sources:  # sources are in (y,x) ascending order
                dy = (ty - sy_) * spacing_y
                dx = (tx - sx_) * spacing_x
                d2 = dy * dy + dx * dx
                if best_sy < 0:
                    best_d2, best_sy, best_sx = d2, sy_, sx_
                elif math.isclose(
                    d2,
                    best_d2,
                    rel_tol=1e-12,
                    abs_tol=abs_floor,
                ):
                    # Explicit tie: sources are visited in lex order, keep
                    # the first (smallest coordinate).
                    if (sy_, sx_) < (best_sy, best_sx):
                        best_d2, best_sy, best_sx = d2, sy_, sx_
                elif d2 < best_d2:
                    best_d2, best_sy, best_sx = d2, sy_, sx_
            dist[ty][tx] = math.sqrt(best_d2)
            ny[ty][tx] = best_sy
            nx[ty][tx] = best_sx
    return ReferenceEDT(distances=dist, nearest_y=ny, nearest_x=nx)
