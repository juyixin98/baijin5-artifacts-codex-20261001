"""Kernel functions.

Only compact-support kernels are offered: every weight is zero beyond one
bandwidth, which keeps the local fit well defined and the effective sample
size interpretable. Kernels operate on *normalised* distances
``u = |x - c| / h`` in ``[0, 1]``.

Reproducibility: kernels are deterministic pure functions of the data and
the recorded bandwidth; there is no randomness at this layer.
"""
from __future__ import annotations

import numpy as np

from app.contract import KernelName


def triangular(u: np.ndarray) -> np.ndarray:
    """Linear (Bartlett) kernel: ``1 - |u|``.

    Boundary optimal for local linear regression (Fan & Gijbels, 1996) and
    the conventional RD default.
    """
    u = np.abs(u)
    return np.where(u <= 1.0, 1.0 - u, 0.0)


def epanechnikov(u: np.ndarray) -> np.ndarray:
    """Quadratic kernel: ``0.75 * (1 - u^2)`` on [-1, 1]."""
    u = np.abs(u)
    return np.where(u <= 1.0, 0.75 * (1.0 - u * u), 0.0)


def uniform(u: np.ndarray) -> np.ndarray:
    """Rectangular kernel: unweighted OLS inside the window."""
    return np.where(np.abs(u) <= 1.0, 1.0, 0.0)


_REGISTRY = {
    KernelName.TRIANGULAR: triangular,
    KernelName.EPANECHNIKOV: epanechnikov,
    KernelName.UNIFORM: uniform,
}


def weights(x: np.ndarray, cutoff: float, bandwidth: float, kernel: KernelName) -> np.ndarray:
    """Return non-negative kernel weights for observations relative to cutoff."""
    if not np.isfinite(bandwidth) or bandwidth <= 0:
        raise ValueError("bandwidth must be a finite positive number")
    fn = _REGISTRY[kernel]
    return fn((x - cutoff) / bandwidth)
