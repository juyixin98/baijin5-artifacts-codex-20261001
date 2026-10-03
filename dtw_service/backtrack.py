"""Shared optimal-path backtracking.

Both the dense and the banded accumulator expose the same read-only
accessor ``get(i, j) -> float`` (``inf`` for illegal/unreachable cells), so
one backtracker serves both. Tie-breaking is deterministic: predecessors
are tried in the step pattern's declared order and the first predecessor
achieving the minimum accumulated cost wins, which biases toward diagonal
steps under the default pattern.
"""

from __future__ import annotations

import math
from typing import Callable

from dtw_service.constraints import StepPattern

CostAccessor = Callable[[int, int], float]


def backtrack_path(
    get_cost: CostAccessor,
    n: int,
    m: int,
    pattern: StepPattern,
) -> list[tuple[int, int]] | None:
    """Backtrack an optimal path from ``(n-1, m-1)`` to ``(0, 0)``.

    Returns the path in forward order, or ``None`` when the endpoint is
    unreachable (infinite accumulated cost).
    """
    if not math.isfinite(get_cost(n - 1, m - 1)):
        return None
    i, j = n - 1, m - 1
    path = [(i, j)]
    while (i, j) != (0, 0):
        best: tuple[int, int] | None = None
        best_cost = math.inf
        for di, dj in pattern.steps:
            pi, pj = i - di, j - dj
            if pi < 0 or pj < 0:
                continue
            c = get_cost(pi, pj)
            if c < best_cost:
                best_cost = c
                best = (pi, pj)
        if best is None:
            # Defensive: a finite endpoint always has a finite predecessor
            # under a valid recurrence; treat corruption as unreachable.
            return None
        i, j = best
        path.append((i, j))
    path.reverse()
    return path
