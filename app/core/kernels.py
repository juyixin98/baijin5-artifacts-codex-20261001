"""Kernel functions for local polynomial weighting.

Each kernel maps the *absolute scaled distance* ``u = |x - c| / h >= 0`` to a
non-negative weight and is zero (hard compact support) for ``u > 1``.

Reproducibility contract: kernels are pure functions; selection is by the
string name recorded verbatim in every result payload.
"""
from __future__ import annotations

from typing import Callable, Dict

import numpy as np
from numpy.typing import NDArray

KernelFn = Callable[[NDArray[np.float64]], NDArray[np.float64]]


def triangular(u: NDArray[np.float64]) -> NDArray[np.float64]:
    """Triangular (linear) kernel: ``1 - |u|``.

    Boundary-optimal for local-linear regression at the cutoff (Cheng, Fan &
    Marron, 1997) and the conventional default in RD applications.
    """
    u = np.abs(u)
    return np.where(u <= 1.0, 1.0 - u, 0.0)


def epanechnikov(u: NDArray[np.float64]) -> NDArray[np.float64]:
    """Epanechnikov kernel: ``0.75 * (1 - u^2)`` (MSE-optimal interior)."""
    u = np.abs(u)
    return np.where(u <= 1.0, 0.75 * (1.0 - u**2), 0.0)


def uniform(u: NDArray[np.float64]) -> NDArray[np.float64]:
    """Uniform (rectangular) kernel ``1/2`` on [-1, 1] - normalized to 1."""
    u = np.abs(u)
    return np.where(u <= 1.0, 0.5, 0.0)


def tricube(u: NDArray[np.float64]) -> NDArray[np.float64]:
    """Tricube kernel (loess default): ``(70/81)(1 - |u|^3)^3``."""
    u = np.abs(u)
    return np.where(u <= 1.0, (70.0 / 81.0) * (1.0 - u**3) ** 3, 0.0)


KERNELS: Dict[str, KernelFn] = {
    "triangular": triangular,
    "epanechnikov": epanechnikov,
    "uniform": uniform,
    "tricube": tricube,
}

DEFAULT_KERNEL = "triangular"


def get_kernel(name: str) -> KernelFn:
    """Return the kernel function for ``name`` or raise a clear error.

    Fails loudly on unknown kernels — silent fallback to a default would make
    runs non-reproducible.
    """
    if not isinstance(name, str):
        raise TypeError(f"kernel name must be a string, got {type(name).__name__}")
    key = name.strip().lower()
    if key not in KERNELS:
        raise ValueError(
            f"unknown kernel {name!r}; choose one of {sorted(KERNELS)}"
        )
    return KERNELS[key]
