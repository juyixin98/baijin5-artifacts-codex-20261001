"""Synthetic data-generating processes with *known* truth.

Every fixture is an independent oracle for validation: the data and the
answer are both generated here, and this module never calls the estimator
under test. The DGPs cover the four scenarios the contract requires:

* :func:`sharp_jump`        - a genuine jump of known size on curved means;
* :func:`no_jump`           - continuous conditional mean (size = 0);
* :func:`density_sorting`   - jump-free mean but a discontinuous density;
* :func:`sparse_boundary`   - support hole next to the cutoff;
* :func:`discrete_runner`   - lattice/heaped assignment variable.

All DGPs return ``(x, y, truth)`` with a seeded RNG so experiments are
reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DGPResult:
    x: np.ndarray
    y: np.ndarray
    tau: float  # true cutoff jump in the conditional mean (0.0 if continuous)
    name: str
    params: dict


def _noise(rng: np.random.Generator, n: int, scale: float, hetero: bool, x: np.ndarray) -> np.ndarray:
    e = rng.normal(0.0, 1.0, size=n)
    if hetero:
        # Variance grows with |x| -> deliberately defeats homoskedastic SEs.
        e = e * scale * (0.5 + np.abs(x))
    else:
        e = e * scale
    return e


def sharp_jump(
    n: int = 1000,
    *,
    tau: float = 3.0,
    cutoff: float = 0.0,
    slope_left: float = 1.5,
    slope_right: float = 0.8,
    curvature: float = -1.0,
    noise_scale: float = 1.0,
    heteroskedastic: bool = True,
    support: float = 1.0,
    seed: int = 7,
) -> DGPResult:
    """Curved conditional mean with a clean jump: local-linear RD must find tau."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(cutoff - support, cutoff + support, size=n)
    treated = x >= cutoff
    mu = np.where(
        treated,
        slope_right * (x - cutoff) + curvature * (x - cutoff) ** 2,
        slope_left * (x - cutoff) + curvature * (x - cutoff) ** 2,
    )
    y = mu + tau * treated + _noise(rng, n, noise_scale, heteroskedastic, x)
    return DGPResult(
        x, y, float(tau), "sharp_jump",
        {"slope_left": slope_left, "slope_right": slope_right,
         "curvature": curvature, "noise_scale": noise_scale,
         "heteroskedastic": heteroskedastic, "seed": seed},
    )


def no_jump(
    n: int = 1000,
    *,
    cutoff: float = 0.0,
    slope: float = 1.2,
    curvature: float = 0.8,
    noise_scale: float = 1.0,
    heteroskedastic: bool = True,
    support: float = 1.0,
    seed: int = 23,
) -> DGPResult:
    """Continuous kinked mean: the true jump is exactly zero."""
    rng = np.random.default_rng(seed)
    x = rng.uniform(cutoff - support, cutoff + support, size=n)
    mu = slope * (x - cutoff) + curvature * (x - cutoff) ** 2
    y = mu + _noise(rng, n, noise_scale, heteroskedastic, x)
    return DGPResult(
        x, y, 0.0, "no_jump",
        {"slope": slope, "curvature": curvature, "noise_scale": noise_scale,
         "heteroskedastic": heteroskedastic, "seed": seed},
    )


def density_sorting(
    n: int = 1200,
    *,
    cutoff: float = 0.0,
    right_density_multiplier: float = 2.5,
    slope: float = 0.8,
    noise_scale: float = 1.0,
    support: float = 1.0,
    seed: int = 71,
) -> DGPResult:
    """No mean jump, but observations bunch on the right (density jump).

    The marginal x-density jumps at the cutoff while E[y|x] stays continuous,
    exactly the setting a density test should flag without manufacturing a
    false outcome jump.
    """
    rng = np.random.default_rng(seed)
    # Mixture of two uniforms with different mass on each side.
    n_right = int(round(n * right_density_multiplier / (1 + right_density_multiplier)))
    n_left = n - n_right
    xl = rng.uniform(cutoff - support, cutoff, size=n_left)
    xr = rng.uniform(cutoff, cutoff + support, size=n_right)
    x = np.concatenate([xl, xr])
    mu = slope * (x - cutoff)
    y = mu + _noise(rng, x.size, noise_scale, False, x)
    return DGPResult(
        x, y, 0.0, "density_sorting",
        {"right_density_multiplier": right_density_multiplier,
         "slope": slope, "noise_scale": noise_scale, "seed": seed},
    )


def sparse_boundary(
    n: int = 600,
    *,
    cutoff: float = 0.0,
    hole: float = 0.35,
    tau: float = 2.0,
    slope: float = 1.0,
    noise_scale: float = 1.0,
    support: float = 1.0,
    seed: int = 101,
) -> DGPResult:
    """A support hole of half-width ``hole`` around the cutoff.

    Any local estimate would be extrapolation; diagnostics must surface the
    gap and the run should be marked unidentified when no window can reach
    data on both sides.
    """
    rng = np.random.default_rng(seed)
    # Rejection sample to clear the hole.
    x = np.empty(0)
    while x.size < n:
        cand = rng.uniform(cutoff - support, cutoff + support, size=n)
        cand = cand[np.abs(cand - cutoff) >= hole]
        x = np.concatenate([x, cand])[:n]
    treated = x >= cutoff
    mu = slope * (x - cutoff)
    y = mu + tau * treated + _noise(rng, n, noise_scale, False, x)
    return DGPResult(
        x, y, float(tau), "sparse_boundary",
        {"hole": hole, "slope": slope, "noise_scale": noise_scale, "seed": seed},
    )


def discrete_runner(
    n: int = 1000,
    *,
    cutoff: float = 0.0,
    step: float = 0.1,
    tau: float = 2.5,
    slope: float = 1.0,
    heap_extra: int = 60,
    noise_scale: float = 1.0,
    support: float = 1.0,
    seed: int = 202,
) -> DGPResult:
    """Lattice-valued runner with extra mass heaped exactly at the cutoff."""
    rng = np.random.default_rng(seed)
    grid = np.arange(cutoff - support, cutoff + support + step / 2, step)
    x = rng.choice(grid, size=n)
    x = np.concatenate([x, np.full(heap_extra, cutoff)])
    treated = x >= cutoff
    mu = slope * (x - cutoff)
    y = mu + tau * treated + _noise(rng, x.size, noise_scale, False, x)
    return DGPResult(
        x, y, float(tau), "discrete_runner",
        {"step": step, "heap_extra": heap_extra, "slope": slope,
         "noise_scale": noise_scale, "seed": seed},
    )


FIXTURE_REGISTRY = {
    "sharp_jump": sharp_jump,
    "no_jump": no_jump,
    "density_sorting": density_sorting,
    "sparse_boundary": sparse_boundary,
    "discrete_runner": discrete_runner,
}
