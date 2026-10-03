"""Independent reference solver used to cross-check Levinson-Durbin.

This module deliberately does NOT share code with the recursion under
test: it solves the Yule-Walker normal equations directly with
scipy's Levinson-free Toeplitz solver (scipy.linalg.solve_toeplitz,
a superfast Toeplitz LU). Tests and the verification module compare
the two independent solutions.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_toeplitz


def solve_toeplitz_reference(r: np.ndarray, order: int) -> np.ndarray:
    """Solve R a = [E, 0, ..., 0]^T for a = [1, a1, ..., a_order].

    Returns the full coefficient vector with leading 1. Raises
    numpy.linalg.LinAlgError when the Toeplitz matrix is singular —
    the caller maps that to a diagnostic; this function does not hide it.
    """
    r = np.asarray(r, dtype=np.float64)
    if r.shape[0] < order + 1:
        raise ValueError(f"need r[0..{order}], got {r.shape[0]} values")
    if r[0] <= 0.0:
        # Defined zero-energy behaviour, identical to the recursion.
        return np.concatenate(([1.0], np.zeros(order)))
    # Solve the order x order system  R_order * x = -r[1..order]
    rhs = -r[1 : order + 1]
    x = solve_toeplitz((r[:order], r[:order]), rhs)
    return np.concatenate(([1.0], x))
