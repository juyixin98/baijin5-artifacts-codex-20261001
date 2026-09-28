"""Influence-function variance and cluster-robust independent units.

Given the per-row influence contributions ``phi_i`` from the estimation kernel
with ``point = mean_i phi_i`` (cross-fitted nuisance treated as fixed; this is
the standard DML/AIPW asymptotic variance up to first order):

* i.i.d. sampling::

      V_hat = s^2 / n,   s^2 = (1/(n-1)) sum_i (phi_i - point)^2

* cluster sampling with G clusters and cluster sums ``C_g = sum_{i in g} phi_i``::

      estimator = n^-1 sum_g C_g,   independent units are the G clusters
      V_hat = G/(n^2 (G-1)) sum_g (C_g - Cbar)^2,  Cbar = G^-1 sum_g C_g

  (Equivalently ``G s_C^2 / n^2`` where ``s_C^2`` is the unbiased sample
  variance of the cluster sums.) The number of INDEPENDENT UNITS is G, not n.
  The confidence interval uses the t distribution with G-1 degrees of freedom
  for clustered inference and the normal distribution for the i.i.d. case.

The cluster formula is the standard Liang-Zeger-style sandwich for a sample
mean of within-cluster sums; it requires clusters to be the independent
sampling units and G reasonably large. With G small we still return a number
but flag it via ``small_cluster_warning`` — callers must not pretend small-G
inference is well calibrated.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from .contract import ComputationFailure, InputError

Z_975 = float(stats.norm.ppf(0.975))
SMALL_CLUSTER_THRESHOLD = 30


@dataclass(frozen=True)
class InferenceResult:
    point: float
    se: float
    variance: float
    ci_lower: float
    ci_upper: float
    independent_units: int
    clustered: bool
    small_cluster_warning: bool


def iid_variance(scores: np.ndarray, point: float) -> InferenceResult:
    scores = np.asarray(scores, dtype=float)
    n = scores.size
    if n < 2:
        raise ComputationFailure("need at least 2 units for variance estimation")
    centered = scores - point
    s2 = float(centered @ centered / (n - 1))
    variance = s2 / n
    if variance < 0.0 or not np.isfinite(variance):
        raise ComputationFailure(
            "non-finite or negative influence variance",
            details={"variance": variance},
        )
    se = float(np.sqrt(variance))
    return InferenceResult(
        point=point, se=se, variance=variance,
        ci_lower=point - Z_975 * se, ci_upper=point + Z_975 * se,
        independent_units=n, clustered=False, small_cluster_warning=False,
    )


def cluster_variance(scores: np.ndarray, point: float,
                     cluster_id: np.ndarray) -> InferenceResult:
    scores = np.asarray(scores, dtype=float)
    cluster_id = np.asarray(cluster_id)
    n = scores.size
    if cluster_id.shape != (n,):
        raise InputError(
            "cluster id vector is not row-aligned with scores",
            details={"cluster_shape": tuple(cluster_id.shape), "n": n},
        )
    unique, inverse = np.unique(cluster_id, return_inverse=True)
    g = unique.size
    if g < 2:
        raise ComputationFailure(
            "need at least 2 independent clusters for variance estimation",
            details={"clusters": g},
        )
    sums = np.zeros(g)
    np.add.at(sums, inverse, scores)
    cbar = sums.mean()
    variance = float(g * np.sum((sums - cbar) ** 2) / (n ** 2 * (g - 1)))
    if variance < 0.0 or not np.isfinite(variance):
        raise ComputationFailure(
            "non-finite or negative cluster variance",
            details={"variance": variance},
        )
    se = float(np.sqrt(variance))
    crit = float(stats.t.ppf(0.975, df=g - 1))
    return InferenceResult(
        point=point, se=se, variance=variance,
        ci_lower=point - crit * se, ci_upper=point + crit * se,
        independent_units=int(g), clustered=True,
        small_cluster_warning=g < SMALL_CLUSTER_THRESHOLD,
    )
