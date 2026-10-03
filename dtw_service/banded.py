"""Band-storage DTW for large matrices.

Memory layout: row ``i`` of the accumulator stores only the cells inside
the Sakoe-Chiba band, in an array of width ``2 * radius + 1`` where column
``k`` corresponds to matrix column ``j = i - radius + k``. Total memory is
``O(n * radius)`` instead of ``O(n * m)``, and local distances are computed
on the fly so no full distance matrix is ever materialized.

The recurrence, window semantics, and tie-breaking are identical to
``dtw_service.dense``; tests assert both produce the same cost and path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from dtw_service.backtrack import backtrack_path
from dtw_service.constraints import (
    DEFAULT_STEP_PATTERN,
    SakoeChibaWindow,
    StepPattern,
)
from dtw_service.distance import local_distance


class BandedAccumulator:
    """Accumulated-cost storage restricted to the Sakoe-Chiba band."""

    def __init__(self, n: int, radius: int) -> None:
        self.n = n
        self.radius = radius
        self.values = np.full((n, 2 * radius + 1), math.inf)

    def _column(self, i: int, j: int) -> int:
        return j - i + self.radius

    def get(self, i: int, j: int) -> float:
        k = self._column(i, j)
        if i < 0 or i >= self.n or k < 0 or k >= self.values.shape[1]:
            return math.inf
        return float(self.values[i, k])

    def set(self, i: int, j: int, value: float) -> None:
        self.values[i, self._column(i, j)] = value


@dataclass(frozen=True)
class BandedResult:
    cost: float  # inf when the endpoint is unreachable
    path: list[tuple[int, int]] | None
    band_shape: tuple[int, int]  # (n, 2 * radius + 1) actually allocated


def banded_dtw(
    a: np.ndarray,
    b: np.ndarray,
    window: SakoeChibaWindow,
    pattern: StepPattern = DEFAULT_STEP_PATTERN,
) -> BandedResult:
    """Run windowed DTW with band storage over scalar sequences ``a``, ``b``."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("banded_dtw requires non-empty sequences")

    acc = BandedAccumulator(n, window.radius)
    acc.set(0, 0, local_distance(a[0], b[0]))

    for i in range(n):
        j_lo, j_hi = window.j_range(i, m)
        for j in range(j_lo, j_hi + 1):
            if i == 0 and j == 0:
                continue
            best = math.inf
            for di, dj in pattern.steps:
                best = min(best, acc.get(i - di, j - dj))
            if math.isfinite(best):
                acc.set(i, j, local_distance(a[i], b[j]) + best)

    cost = acc.get(n - 1, m - 1)
    path = backtrack_path(acc.get, n, m, pattern)
    return BandedResult(cost=cost, path=path, band_shape=acc.values.shape)
