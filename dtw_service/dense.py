"""Dense dynamic-programming DTW.

Reference-quality core: allocates the full ``n x m`` accumulated-cost
matrix. Used directly for small inputs and as the cross-check oracle for
the banded implementation in tests. For large inputs use
``dtw_service.banded`` — both implement the identical recurrence

    D(i, j) = d(i, j) + min over (di, dj) in steps of D(i-di, j-dj)

restricted to cells inside the Sakoe-Chiba window, with
``D(0, 0) = d(0, 0)`` and ``inf`` outside the band.
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
from dtw_service.distance import distance_matrix, local_distance


@dataclass(frozen=True)
class DenseResult:
    cost: float  # inf when the endpoint is unreachable
    path: list[tuple[int, int]] | None
    accumulated: np.ndarray  # full accumulated-cost matrix, inf outside band


def dense_dtw(
    a: np.ndarray,
    b: np.ndarray,
    window: SakoeChibaWindow,
    pattern: StepPattern = DEFAULT_STEP_PATTERN,
) -> DenseResult:
    """Run dense windowed DTW over scalar sequences ``a`` and ``b``."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        raise ValueError("dense_dtw requires non-empty sequences")

    local = distance_matrix(a, b)
    accumulated = np.full((n, m), math.inf)
    accumulated[0, 0] = local_distance(a[0], b[0])

    for i in range(n):
        j_lo, j_hi = window.j_range(i, m)
        for j in range(j_lo, j_hi + 1):
            if i == 0 and j == 0:
                continue
            best = math.inf
            for di, dj in pattern.steps:
                pi, pj = i - di, j - dj
                if pi >= 0 and pj >= 0:
                    best = min(best, accumulated[pi, pj])
            if math.isfinite(best):
                accumulated[i, j] = local[i, j] + best

    cost = float(accumulated[n - 1, m - 1])
    path = backtrack_path(
        lambda i, j: float(accumulated[i, j]), n, m, pattern
    )
    return DenseResult(cost=cost, path=path, accumulated=accumulated)
