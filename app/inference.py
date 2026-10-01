"""Inference: robust standard errors, confidence intervals, wild bootstrap.

Variance estimator
------------------
Each side is an independent weighted regression. The jump is
``tau = e0'(beta_R - beta_L)`` with ``e0 = (1, 0)``; because the two sides
use disjoint observations their covariance is zero, so

    Var(tau) = e0' V_L e0 + e0' V_R e0.

``V_s`` is the heteroskedasticity-robust sandwich

    V = (X'WX)^-1 [ sum_i w_i^2 r_i^2 x_i x_i' ] (X'WX)^-1

with the small-sample correction

* HC1: factor ``n / (n - 2)``;
* HC3 (default): residual inflation ``r_i -> r_i / (1 - h_ii)``, the more
  conservative, better-behaved choice in the small boundary samples RD
  designs typically have.

Bias handling
-------------
A local-linear estimator has smoothing bias of order ``O(h^2)``. We do *not*
subtract a plug-in bias estimate by default -- automatic higher-order
corrections are noisy and can inflate MSE. Instead the response reports the
bandwidth and effective sample per side, the identification range (window
actually used), exposes ``bandwidth_multiplier`` for sensitivity analysis,
and attaches a plain-language bias note to every estimate.

Wild bootstrap
--------------
Rademacher wild bootstrap reproduces heteroskedasticity without imposing
homoskedasticity. Two distinct resampling schemes are used: an unrestricted
DGP yields the percentile CI; a restricted-null DGP (treated-side mean
shifted by -tau_obs) yields the studentized p-value. With cluster labels the
multiplier is shared within clusters (cluster wild / CRV). Seeded, hence
reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from app.estimator import SideResult

TWO_COEFS = 2


@dataclass(frozen=True)
class RobustResult:
    se: float
    ci_low: float
    ci_high: float
    z: float
    p_value: float


def _meat(result: SideResult, hc3: bool, scale: float) -> np.ndarray:
    r2 = result.resid**2
    if hc3:
        r2 = r2 / (1.0 - result.hat) ** 2
    X = result.design
    w = result.w
    return (scale * (w**2)[:, None] * r2[:, None] * X).T @ X


def _vcov(result: SideResult, kind: str) -> np.ndarray:
    Xw = result.design * np.sqrt(result.w)[:, None]
    inv = np.linalg.inv(Xw.T @ Xw)
    n = result.n
    if kind == "hc1":
        scale = n / max(1, n - TWO_COEFS)
        meat = _meat(result, hc3=False, scale=scale)
    else:  # hc3
        meat = _meat(result, hc3=True, scale=1.0)
    return inv @ meat @ inv


def jump_standard_error(left: SideResult, right: SideResult, kind: str) -> float:
    v_l = _vcov(left, kind)
    v_r = _vcov(right, kind)
    var_tau = float(v_l[0, 0] + v_r[0, 0])
    if var_tau <= 0 or not np.isfinite(var_tau):
        raise np.linalg.LinAlgError(f"non-positive tau variance: {var_tau}")
    return float(np.sqrt(var_tau))


def robust_inference(
    tau: float, left: SideResult, right: SideResult, kind: str, alpha: float
) -> RobustResult:
    se = jump_standard_error(left, right, kind)
    zcrit = float(stats.norm.ppf(1.0 - alpha / 2.0))
    z = tau / se
    p = float(2.0 * (1.0 - stats.norm.cdf(abs(z))))
    return RobustResult(
        se=se,
        ci_low=tau - zcrit * se,
        ci_high=tau + zcrit * se,
        z=float(z),
        p_value=p,
    )


def _intercept_loading(result: SideResult) -> np.ndarray:
    """Row q with intercept(fit on y*) = q @ y* for the fixed weighted design."""
    Xw = result.design * np.sqrt(result.w)[:, None]
    inv = np.linalg.inv(Xw.T @ Xw)
    return (inv @ (result.design * result.w[:, None]).T)[0]


def _multiplier_draws(
    reps: int, n: int, rng: np.random.Generator, labels: np.ndarray | None
) -> np.ndarray:
    if labels is None:
        return rng.choice(np.array([-1.0, 1.0]), size=(reps, n))
    uniq, inv = np.unique(np.asarray(labels), return_inverse=True)
    group = rng.choice(np.array([-1.0, 1.0]), size=(reps, uniq.size))
    return group[:, inv]


def wild_bootstrap(
    tau_obs: float,
    se_obs: float | None,
    left: SideResult,
    right: SideResult,
    reps: int,
    seed: int | None,
    left_clusters: np.ndarray | None,
    right_clusters: np.ndarray | None,
) -> dict:
    if reps <= 0:
        return {"reps": 0}
    rng = np.random.default_rng(seed)
    q_l = _intercept_loading(left)
    q_r = _intercept_loading(right)

    V_l = _multiplier_draws(reps, left.n, rng, left_clusters)
    V_r = _multiplier_draws(reps, right.n, rng, right_clusters)

    # Unrestricted draws -> jump distribution centred near tau_obs (CI).
    y_alt_l = left.fitted + V_l * left.resid
    y_alt_r = right.fitted + V_r * right.resid
    tau_alt = y_alt_r @ q_r - y_alt_l @ q_l
    ci_low = float(np.quantile(tau_alt, 0.025))
    ci_high = float(np.quantile(tau_alt, 0.975))

    # Restricted-null draws -> jumps centred at zero (studentized p-value).
    y_null_r = right.fitted - tau_obs + V_r * right.resid
    tau_null = y_null_r @ q_r - y_alt_l @ q_l
    if se_obs is not None and se_obs > 0:
        p_boot = float(np.mean(np.abs(tau_null / se_obs) >= abs(tau_obs / se_obs)))
    else:
        p_boot = float(np.mean(np.abs(tau_null) >= abs(tau_obs)))

    return {
        "reps": reps,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value": max(p_boot, 1.0 / reps),
        "clustered": left_clusters is not None or right_clusters is not None,
        "null_jump_std": float(np.std(tau_null)),
        "alt_jump_std": float(np.std(tau_alt)),
    }


BIAS_NOTE = (
    "Local-linear smoothing bias is O(h^2) and is not plug-in corrected "
    "(deliberately: automatic higher-order corrections are unstable in small "
    "boundary samples). Inspect bandwidth and per-side effective N, sweep "
    "bandwidth_multiplier, and treat estimates where the window barely spans "
    "the slope as sensitivity-dependent. The nominal robust-sandwich CI does "
    "not by itself cover smoothing bias."
)
