"""Weighted least squares engine for local-linear regression.

Design matrix for one side of the cutoff::

    X = [[1, (x_i - c)], ...]          (n x 2)

so the fitted intercept *is* the estimated conditional mean at the cutoff and
the slope is the local linear derivative.  Weights ``w`` are kernel weights
(zero outside the bandwidth).

Variance estimators provided:

- ``const`` : classic homoskedastic WLS formula  sigma^2 (X'WX)^{-1}
- ``HC1``   : heteroskedasticity-robust sandwich, small-sample corrected
- ``HC2``   : robust sandwich with leverage correction (used for the reported
              inference, as recommended for weighted local regressions)

Linear-algebra failures (singular design, rank < 2, fewer than 3 positive
weight observations) raise :class:`~app.core.contracts.FitError` rather than
silently emitting NaNs or a degenerate "success".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .contracts import FitError

Array = NDArray[np.float64]

MIN_EFFECTIVE_OBS = 3.0
# Condition-number guard for the weighted design. Discrete/heaped data can
# produce an X'WX that is formally full rank yet numerically singular, which
# would yield spuriously zero standard errors.
MAX_CONDITION = 1e8


@dataclass(frozen=True)
class WLSResult:
    intercept: float          # fitted value at the cutoff (mu_hat)
    slope: float              # local derivative d E[Y|X]/dX at cutoff
    se_homoskedastic: float
    se_hc1: float
    se_hc2: float
    n: int                    # number of observations with positive weight
    sum_weights: float        # kernel-effective sample size proxy (sum w)
    sse: float                # weighted residual sum of squares
    rank: int


def weighted_least_squares(
    x_centered: Array,
    y: Array,
    weights: Array,
) -> WLSResult:
    """Solve ``argmin_b sum_i w_i (y_i - b0 - b1 d_i)^2``.

    Parameters
    ----------
    x_centered
        ``d_i = x_i - c`` for one side only.
    y
        Outcomes aligned with ``x_centered``.
    weights
        Non-negative kernel weights (exact zeros outside the support).
    """
    d = np.asarray(x_centered, dtype=np.float64)
    yv = np.asarray(y, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)

    if d.shape != yv.shape or d.shape != w.shape:
        raise FitError(f"shape mismatch: d={d.shape}, y={yv.shape}, w={w.shape}")
    if not np.all(np.isfinite(d)) or not np.all(np.isfinite(yv)):
        raise FitError("non-finite values in x or y")
    if np.any(w < 0.0):
        raise FitError("kernel weights must be non-negative")

    positive = w > 0.0
    n_pos = int(np.count_nonzero(positive))
    if n_pos < MIN_EFFECTIVE_OBS:
        raise FitError(
            f"not enough effective observations: {n_pos} < {int(MIN_EFFECTIVE_OBS)}"
        )

    d, yv, w = d[positive], yv[positive], w[positive]
    n = d.size
    sqrt_w = np.sqrt(w)
    X = np.column_stack([np.ones(n), d])
    Xw = X * sqrt_w[:, None]
    yw = yv * sqrt_w

    try:
        # lstsq handles near-singular designs; rank is checked explicitly.
        beta, _, rank, _ = np.linalg.lstsq(Xw, yw, rcond=None)
    except np.linalg.LinAlgError as exc:  # pragma: no cover - defensive
        raise FitError(f"linear algebra failure: {exc}") from exc

    if rank < 2:
        raise FitError(f"rank-deficient local design (rank={rank} < 2)")

    XtWX = Xw.T @ Xw
    try:
        XtWX_inv = np.linalg.inv(XtWX)
    except np.linalg.LinAlgError as exc:
        raise FitError(f"singular X'WX matrix: {exc}") from exc

    # Scale-free condition number: normalise by the diagonal so that the
    # intercept/slope unit difference does not dominate the ratio.
    diag = np.sqrt(np.diag(XtWX))
    if np.any(diag <= 0.0):
        raise FitError("zero diagonal in X'WX (degenerate weighted design)")
    scaled = XtWX / np.outer(diag, diag)
    cond = float(np.linalg.cond(scaled))
    if not np.isfinite(cond) or cond > MAX_CONDITION:
        raise FitError(
            f"ill-conditioned local design (cond={cond:.3g} > {MAX_CONDITION:.0e}); "
            "widen the bandwidth"
        )

    resid = yv - X @ beta
    wresid = w * resid
    sse = float(resid @ wresid)

    # Homoskedastic estimator: sigma^2 (X'WX)^-1, sigma^2 = SSE/(n-p).
    dof = max(n - 2, 1)
    sigma2 = sse / dof
    var_const = sigma2 * XtWX_inv

    # Robust sandwich: (X'WX)^-1 X' W diag(e_i^2) W X (X'WX)^-1
    XtW = X.T * w[None, :]                    # (2, n), columns w_i * x_i
    score_sq = (resid**2)[None, :]            # (1, n)
    meat_const = (XtW * score_sq) @ XtW.T     # X' W diag(e^2) W X
    # NOTE: weights enter twice (W on each side) because the score of the
    # *weighted* objective is X'W e; that is the sandwich for the WLS solution.
    var_hc1_base = XtWX_inv @ meat_const @ XtWX_inv

    # Leverage under the weighted fit: h_i = w_i x_i' (X'WX)^-1 x_i
    leverage = np.einsum("ij,jk,ik->i", X, XtWX_inv, X) * w
    leverage = np.clip(leverage, 0.0, 0.999999)
    meat_hc2 = (XtW * (resid**2 / (1.0 - leverage))[None, :]) @ XtW.T
    var_hc2 = XtWX_inv @ meat_hc2 @ XtWX_inv

    def _se0(m: Array) -> float:
        value = float(m[0, 0])
        return float(np.sqrt(value)) if value > 0.0 else float("nan")

    return WLSResult(
        intercept=float(beta[0]),
        slope=float(beta[1]),
        se_homoskedastic=_se0(var_const),
        se_hc1=_se0(var_hc1_base) * np.sqrt(n / dof),
        se_hc2=_se0(var_hc2),
        n=n,
        sum_weights=float(np.sum(w)),
        sse=sse,
        rank=int(rank),
    )
