"""Fit residuals between the input matrix and the inferred tree.

Residuals are always computed against the tree as actually emitted (after
any declared negative-branch handling), so clamping can never hide fit error.
"""

from __future__ import annotations

import math

from .models import DistanceMatrix, PairResidual, ResidualReport


def compute_residuals(
    dm: DistanceMatrix,
    tree_distances: dict[tuple[str, str], float],
) -> ResidualReport:
    pairs: list[PairResidual] = []
    n = dm.n
    for i in range(n):
        for j in range(i + 1, n):
            a, b = dm.labels[i], dm.labels[j]
            key = tuple(sorted((a, b)))
            if key not in tree_distances:
                raise KeyError(f"tree is missing leaf pair {key}")
            fitted = tree_distances[key]
            observed = float(dm.values[i, j])
            pairs.append(PairResidual(a, b, observed, fitted, observed - fitted))

    total = sum(abs(p.residual) for p in pairs)
    max_abs = max((abs(p.residual) for p in pairs), default=0.0)
    rmse = math.sqrt(sum(p.residual ** 2 for p in pairs) / len(pairs)) if pairs else 0.0
    return ResidualReport(pairs=pairs, total_absolute=total, max_absolute=max_abs, rmse=rmse)
