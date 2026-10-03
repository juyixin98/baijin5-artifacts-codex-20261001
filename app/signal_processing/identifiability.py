"""Identifiability assessment of the FIR regression problem.

A model order L is identifiable from the excitation only if the design
matrix has full column rank L. Spectrally degenerate excitation (e.g. a
single sinusoid, which spans only a 2-dimensional subspace) makes the
normal equations singular and the channel unrecoverable without
regularization — this must be reported, not silently solved.

The numerical rank uses the standard SVD tolerance
``s_max * max(M, L) * eps`` (the same default as ``numpy.linalg.matrix_rank``)
and the tolerance is reported alongside the rank so decisions are auditable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class IdentifiabilityReport:
    n_rows: int
    n_columns: int
    singular_values: list[float] = field(compare=False)
    rank: int
    condition_number: float  # inf when the smallest singular value is ~0
    tolerance: float
    identifiable: bool

    def to_dict(self) -> dict:
        return {
            "n_rows": self.n_rows,
            "n_columns": self.n_columns,
            "singular_values": self.singular_values,
            "rank": self.rank,
            "condition_number": self.condition_number,
            "tolerance": self.tolerance,
            "identifiable": self.identifiable,
        }


def assess_identifiability(matrix: np.ndarray) -> IdentifiabilityReport:
    """Compute the numerical-rank report for a design matrix."""
    a = np.asarray(matrix, dtype=float)
    n_rows, n_cols = a.shape
    singular_values = np.linalg.svd(a, compute_uv=False)
    s_max = float(singular_values[0]) if singular_values.size else 0.0
    tolerance = s_max * max(a.shape) * np.finfo(float).eps
    rank = int(np.count_nonzero(singular_values > tolerance))
    s_min = float(singular_values[-1]) if singular_values.size else 0.0
    condition = float(s_max / s_min) if s_min > 0.0 else float("inf")
    return IdentifiabilityReport(
        n_rows=n_rows,
        n_columns=n_cols,
        singular_values=[float(v) for v in singular_values],
        rank=rank,
        condition_number=condition,
        tolerance=tolerance,
        identifiable=rank == n_cols,
    )
