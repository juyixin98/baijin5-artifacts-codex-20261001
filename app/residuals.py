"""Residual report: how well the emitted tree fits the observed distances.

Patristic distances are recomputed from the tree adjacency (BFS over a tree,
so each query is exact), then compared pair-by-pair against the input
matrix. This is what makes negative-branch handling honest: if a branch was
clamped, the tree distances change and the residual grows accordingly.
"""

from __future__ import annotations

import math
from collections import deque

from .models import PairResidual, ResidualReport
from .nj import NJResult


def patristic_distance(adjacency: dict[int, dict[int, float]], start: int, goal: int) -> float:
    """Exact path distance between two nodes on a weighted tree."""
    if start == goal:
        return 0.0
    visited = {start}
    queue: deque[tuple[int, float]] = deque([(start, 0.0)])
    while queue:
        node, dist = queue.popleft()
        for neighbor, weight in adjacency.get(node, {}).items():
            if neighbor in visited:
                continue
            if neighbor == goal:
                return dist + weight
            visited.add(neighbor)
            queue.append((neighbor, dist + weight))
    raise KeyError(f"nodes {start} and {goal} are not connected")


def compute_residuals(
    labels: list[str], matrix: list[list[float]], result: NJResult
) -> ResidualReport:
    leaf_ids = sorted(result.leaf_labels)
    per_pair: list[PairResidual] = []
    for pos_i, i in enumerate(leaf_ids):
        for j in leaf_ids[pos_i + 1 :]:
            tree_d = patristic_distance(result.adjacency, i, j)
            observed = matrix[i][j]
            per_pair.append(
                PairResidual(
                    taxon_i=labels[i],
                    taxon_j=labels[j],
                    observed=observed,
                    tree=tree_d,
                    abs_diff=abs(observed - tree_d),
                )
            )
    if not per_pair:
        return ResidualReport(sum_abs=0.0, max_abs=0.0, mean_abs=0.0, rms=0.0, per_pair=[])
    diffs = [p.abs_diff for p in per_pair]
    sum_abs = sum(diffs)
    rms = math.sqrt(sum(d * d for d in diffs) / len(diffs))
    return ResidualReport(
        sum_abs=sum_abs,
        max_abs=max(diffs),
        mean_abs=sum_abs / len(diffs),
        rms=rms,
        per_pair=per_pair,
    )
