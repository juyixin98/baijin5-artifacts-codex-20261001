"""Synthetic data-generating processes for RD validation.

All DGPs are local and seeded; no real business data is used anywhere.

Scenarios (the validation matrix required by the contract):

- ``sharp_jump``        : known discontinuity ``tau`` at the cutoff
- ``no_jump``           : continuous regression function (tau = 0)
- ``density_discontinuity`` : McCrary-style sorting - density jumps,
                          conditional mean stays continuous
- ``sparse_boundary``   : very few observations close to the cutoff
- ``heaped_discrete``   : coarse running variable with pile-up at cutoff
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class DGPResult:
    x: Array
    y: Array
    cutoff: float
    true_tau: float
    name: str
    params: Dict[str, float]

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "cutoff": self.cutoff,
            "true_tau": self.true_tau,
            "params": self.params,
            "n": int(self.x.size),
        }


def _default_rng(seed: int | np.random.Generator) -> np.random.Generator:
    if isinstance(seed, np.random.Generator):
        return seed
    return np.random.default_rng(seed)


def sharp_jump(
    n: int = 1000,
    *,
    tau: float = 10.0,
    cutoff: float = 0.0,
    slope: float = 1.5,
    curvature: float = 0.8,
    noise: float = 1.0,
    seed: int | np.random.Generator = 101,
) -> DGPResult:
    """Continuous curvature + a sharp constant jump on the right."""
    rng = _default_rng(seed)
    x = rng.uniform(-1.0, 1.0, size=n)
    mu = slope * x + curvature * x**2
    y = mu + np.where(x >= cutoff, tau, 0.0) + rng.normal(0.0, noise, size=n)
    return DGPResult(x, y, cutoff, float(tau), "sharp_jump",
                     {"n": n, "tau": tau, "slope": slope,
                      "curvature": curvature, "noise": noise, "seed": _seed(seed)})


def no_jump(
    n: int = 1000,
    *,
    cutoff: float = 0.0,
    slope: float = -2.0,
    curvature: float = 3.0,
    noise: float = 1.0,
    seed: int | np.random.Generator = 202,
) -> DGPResult:
    """Continuous, smooth conditional mean — true jump exactly zero."""
    rng = _default_rng(seed)
    x = rng.uniform(-1.0, 1.0, size=n)
    y = slope * x + curvature * x**2 + rng.normal(0.0, noise, size=n)
    return DGPResult(x, y, cutoff, 0.0, "no_jump",
                     {"n": n, "slope": slope, "curvature": curvature,
                      "noise": noise, "seed": _seed(seed)})


def density_discontinuity(
    n: int = 1200,
    *,
    cutoff: float = 0.0,
    right_share: float = 0.75,
    slope: float = 1.0,
    noise: float = 1.0,
    seed: int | np.random.Generator = 303,
) -> DGPResult:
    """Density jumps at the cutoff but the conditional mean does not."""
    rng = _default_rng(seed)
    n_right = int(round(n * right_share))
    n_left = n - n_right
    x = np.concatenate([
        rng.uniform(-1.0, cutoff, size=n_left),
        rng.uniform(cutoff, 1.0, size=n_right),
    ])
    rng.shuffle(x)
    y = slope * x + rng.normal(0.0, noise, size=n)
    return DGPResult(x, y, cutoff, 0.0, "density_discontinuity",
                     {"n": n, "right_share": right_share, "slope": slope,
                      "noise": noise, "seed": _seed(seed)})


def sparse_boundary(
    n: int = 600,
    *,
    cutoff: float = 0.0,
    inner_gap: float = 0.25,
    tau: float = 4.0,
    slope: float = 1.0,
    noise: float = 1.0,
    seed: int | np.random.Generator = 404,
) -> DGPResult:
    """No data within ``(-inner_gap, inner_gap)`` around the cutoff.

    Small bandwidths are structurally infeasible; the estimator must report
    that explicitly rather than extrapolating silently.
    """
    rng = _default_rng(seed)
    half = n // 2
    x = np.concatenate([
        rng.uniform(-1.0, -inner_gap, size=half),
        rng.uniform(inner_gap, 1.0, size=n - half),
    ])
    rng.shuffle(x)
    y = slope * x + np.where(x >= cutoff, tau, 0.0) + rng.normal(0, noise, n)
    return DGPResult(x, y, cutoff, float(tau), "sparse_boundary",
                     {"n": n, "inner_gap": inner_gap, "tau": tau, "slope": slope,
                      "noise": noise, "seed": _seed(seed)})


def heaped_discrete(
    n: int = 1000,
    *,
    cutoff: float = 0.0,
    step: float = 0.1,
    heap_multiplier: float = 6.0,
    tau: float = 5.0,
    slope: float = 1.0,
    noise: float = 1.0,
    seed: int | np.random.Generator = 505,
) -> DGPResult:
    """Running variable recorded on a coarse grid with a pile-up at cutoff."""
    rng = _default_rng(seed)
    grid = np.arange(-1.0, 1.0 + step / 2, step)
    base = rng.integers(0, grid.size, size=n)
    x = grid[base]
    # Inflate mass exactly at the cutoff value.
    heap_size = int(n * 0.02 * heap_multiplier)
    x[:heap_size] = cutoff
    rng.shuffle(x)
    y = slope * x + np.where(x >= cutoff, tau, 0.0) + rng.normal(0, noise, n)
    return DGPResult(x, y, cutoff, float(tau), "heaped_discrete",
                     {"n": n, "step": step, "heap_multiplier": heap_multiplier,
                      "tau": tau, "slope": slope, "noise": noise,
                      "seed": _seed(seed)})


def _seed(seed: int | np.random.Generator) -> int:
    return int(seed) if isinstance(seed, (int, np.integer)) else -1


DGPs: Dict[str, Callable[..., DGPResult]] = {
    "sharp_jump": sharp_jump,
    "no_jump": no_jump,
    "density_discontinuity": density_discontinuity,
    "sparse_boundary": sparse_boundary,
    "heaped_discrete": heaped_discrete,
}
