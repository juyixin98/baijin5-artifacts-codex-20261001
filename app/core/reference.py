"""Independent reference regressions.

These OLS routines are deliberately written from scratch and are *not* used
by the estimation kernel to produce its headline numbers. Tests call them to
verify the kernel independently, and the estimator attaches the adjusted
model's treatment coefficient as a cross-check in the result payload.

Standard errors:

* model-based: sigma^2 (Z'Z)^-1
* HC1 sandwich:    n/(n-k) (Z'Z)^-1  sum_i e_i^2 z_i z_i'  (Z'Z)^-1
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def ols_fit(y: np.ndarray, Z: np.ndarray, hc1: bool = True) -> dict:
    y = np.asarray(y, dtype=float)
    Z = np.asarray(Z, dtype=float)
    n, k = Z.shape
    if n <= k:
        raise ValueError(f"OLS needs n>k, got n={n}, k={k}")

    beta, _, rank, _ = np.linalg.lstsq(Z, y, rcond=None)
    if rank < k:
        raise np.linalg.LinAlgError(f"rank-deficient design: rank={rank} < k={k}")

    fitted = Z @ beta
    resid = y - fitted
    rss = float(resid @ resid)
    sigma2 = rss / (n - k)
    ztz_inv = np.linalg.inv(Z.T @ Z)

    model_vcov = sigma2 * ztz_inv
    # HC1 sandwich middle term: sum_i e_i^2 z_i z_i'.
    meat = np.zeros((k, k))
    for zi, ei in zip(Z, resid):
        meat += (ei * ei) * np.outer(zi, zi)
    hc1_vcov = (n / (n - k)) * ztz_inv @ meat @ ztz_inv

    chosen_vcov = hc1_vcov if hc1 else model_vcov
    se = np.sqrt(np.diag(chosen_vcov))
    tss = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - rss / tss if tss > 0 else float("nan")

    return {
        "beta": beta,
        "se": se,
        "se_model": np.sqrt(np.diag(model_vcov)),
        "se_hc1": np.sqrt(np.diag(hc1_vcov)),
        "vcov": chosen_vcov,
        "resid": resid,
        "sigma2": sigma2,
        "r_squared": r_squared,
        "n": n,
        "k": k,
        "rank": int(rank),
    }


def treatment_inference(fit: dict, treatment_index: int = 1, z_alpha: float = 1.959963984540054) -> dict:
    """Normal-approx inference on one coefficient of a fitted OLS model."""
    b = float(fit["beta"][treatment_index])
    se = float(fit["se"][treatment_index])
    z = b / se
    p = float(2.0 * stats.norm.sf(abs(z)))
    return {
        "beta_treatment": b,
        "se_treatment": se,
        "z_stat": z,
        "p_value": p,
        "ci_low": b - z_alpha * se,
        "ci_high": b + z_alpha * se,
    }
