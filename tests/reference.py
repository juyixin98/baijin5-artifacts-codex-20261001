"""Independent reference: exhaustive enumeration of all legal DTW paths.

This module deliberately shares no code with ``dtw_service``'s DP cores.
It recursively enumerates every monotone path from ``(0, 0)`` to
``(n-1, m-1)`` using steps ``{(1,1), (2,1), (1,2)}`` inside the band
``|i - j| <= radius``, scoring each path as the sum of ``|a_i - b_j|``
over its cells. Feasible only for short sequences — exactly what the
contract's "short-sequence exhaustive reference" verification needs.
"""

from __future__ import annotations

import math

STEPS = ((1, 1), (2, 1), (1, 2))


def enumerate_paths(
    a: list[float], b: list[float], radius: int
) -> list[tuple[list[tuple[int, int]], float]]:
    """Return all ``(path, cost)`` pairs of legal paths; empty if none."""
    n, m = len(a), len(b)
    results: list[tuple[list[tuple[int, int]], float]] = []

    def visit(i: int, j: int, path: list[tuple[int, int]], cost: float) -> None:
        if (i, j) == (n - 1, m - 1):
            results.append((list(path), cost))
            return
        for di, dj in STEPS:
            ni, nj = i + di, j + dj
            if ni >= n or nj >= m:
                continue
            if abs(ni - nj) > radius:
                continue
            path.append((ni, nj))
            visit(ni, nj, path, cost + abs(a[ni] - b[nj]))
            path.pop()

    if n == 0 or m == 0:
        return results
    visit(0, 0, [(0, 0)], abs(a[0] - b[0]))
    return results


def reference_min_cost(
    a: list[float], b: list[float], radius: int
) -> float:
    """Minimum path cost by exhaustive enumeration; ``inf`` if unreachable."""
    paths = enumerate_paths(a, b, radius)
    if not paths:
        return math.inf
    return min(cost for _, cost in paths)
