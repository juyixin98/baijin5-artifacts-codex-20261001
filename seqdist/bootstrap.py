"""Site-resampling (bootstrap) confidence intervals.

Resampling contract:
    * The random source is numpy.random.Generator created fresh per call as
      np.random.default_rng(seed); nothing else in the process can perturb it.
    * Replicate i draws n indices with rng.integers(0, n, size=n), where n is
      the number of valid sites; replicates are drawn sequentially in one
      loop, so results are fully determined by (site_codes, model,
      n_replicates, alpha, seed).
    * Each replicate is re-corrected through distance.estimate_from_codes.
      Replicates outside the model's valid domain are counted as saturated
      and excluded from the percentile interval; they are never clamped or
      absolute-valued.
    * The interval is the [100*(alpha/2), 100*(1-alpha/2)] percentiles of the
      valid replicate values (numpy 'linear' interpolation).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .distance import EstimateStatus, estimate_from_codes

MIN_OK_REPLICATES = 2


@dataclass(frozen=True)
class BootstrapResult:
    model: str
    n_replicates: int
    alpha: float
    seed: int
    n_ok: int
    n_saturated: int
    lower: float | None
    upper: float | None
    status: EstimateStatus

    @property
    def confidence_level(self) -> float:
        return 1.0 - self.alpha


def bootstrap_ci(
    site_codes: np.ndarray,
    model: str,
    *,
    n_replicates: int,
    alpha: float,
    seed: int,
) -> BootstrapResult:
    """Percentile bootstrap CI over valid sites with a fixed random source."""
    codes = np.asarray(site_codes, dtype=np.int64)
    n = codes.size
    rng = np.random.default_rng(seed)
    values: list[float] = []
    n_saturated = 0
    for _ in range(n_replicates):
        idx = rng.integers(0, n, size=n)
        est = estimate_from_codes(codes[idx], model)
        if est.status is EstimateStatus.OK:
            values.append(est.value)  # type: ignore[arg-type]
        else:
            n_saturated += 1
    n_ok = n_replicates - n_saturated
    if n_ok < MIN_OK_REPLICATES:
        return BootstrapResult(
            model=model,
            n_replicates=n_replicates,
            alpha=alpha,
            seed=seed,
            n_ok=n_ok,
            n_saturated=n_saturated,
            lower=None,
            upper=None,
            status=EstimateStatus.NON_ESTIMABLE,
        )
    lo, hi = np.percentile(np.asarray(values), [100.0 * alpha / 2.0, 100.0 * (1.0 - alpha / 2.0)])
    return BootstrapResult(
        model=model,
        n_replicates=n_replicates,
        alpha=alpha,
        seed=seed,
        n_ok=n_ok,
        n_saturated=n_saturated,
        lower=float(lo),
        upper=float(hi),
        status=EstimateStatus.OK,
    )
