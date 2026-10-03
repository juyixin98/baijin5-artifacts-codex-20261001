"""Independent reference solver: direct Toeplitz solve of the normal equations.

This module exists so the Levinson-Durbin recursion can be cross-checked
against an implementation that shares no code with it (SciPy's Levinson
recursion on the Toeplitz system, ``scipy.linalg.solve_toeplitz``). The
API exposes the comparison as an opt-in ``verify_toeplitz`` cross-check;
the test-suite uses it as a reference answer.
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import solve_toeplitz


def solve_normal_equations_toeplitz(r: np.ndarray, order: int) -> np.ndarray:
    """Solve R a = -r[1..p] directly; returns coefficients [1, a1, ..., ap].

    Raises ``numpy.linalg.LinAlgError`` when the Toeplitz matrix is
    singular (exactly the ill-conditioned case the recursion diagnoses).
    """
    r = np.asarray(r, dtype=float)
    if r.size < order + 1:
        raise ValueError(f"need r[0..{order}], got {r.size} values")
    c = r[:order]
    rhs = -r[1 : order + 1]
    a = solve_toeplitz((c, c), rhs)
    return np.concatenate([[1.0], a])
