"""Ordinary least squares primitives used by the regression estimators.

Implements explicit matrix algebra (normal equations + meat/ bread sandwich)
rather than calling a stats package, so every variance formula is auditable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import ErrorCode, EstimationError, SEType


@dataclass(frozen=True)
class OLSFit:
    beta: np.ndarray            # coefficient vector, shape (p,)
    residuals: np.ndarray       # shape (n,)
    xtx_inv: np.ndarray         # (X'X)^-1, shape (p, p)
    rank: int
    n: int
    p: int
    sigma2: float               # residual variance with n-p denominator
    r_squared: float
    fitted: np.ndarray


def ols(x: np.ndarray, y: np.ndarray, *, label: str = "design") -> OLSFit:
    """Fit OLS by normal equations with an explicit rank check.

    Raises ``COLLINEAR_COVARIATES`` when the design matrix is rank-deficient.
    """
    n, p = x.shape
    if n <= p:
        raise EstimationError(
            ErrorCode.INSUFFICIENT_SAMPLE,
            f"{label}: n={n} observations but p={p} columns "
            "(need n > p after intercept)",
            {"n": n, "p": p},
        )
    xtx = x.T @ x
    try:
        xtx_inv = np.linalg.inv(xtx)
    except np.linalg.LinAlgError as exc:
        raise EstimationError(
            ErrorCode.COLLINEAR_COVARIATES,
            f"{label}: singular Gram matrix; covariates are collinear",
        ) from exc

    rank = int(np.linalg.matrix_rank(x))
    if rank < p:
        raise EstimationError(
            ErrorCode.COLLINEAR_COVARIATES,
            f"{label}: design matrix rank {rank} < {p} columns; "
            "remove redundant/constant covariates",
            {"rank": rank, "p": p},
        )

    beta = xtx_inv @ (x.T @ y)
    fitted = x @ beta
    residuals = y - fitted
    dof = n - p
    rss = float(residuals @ residuals)
    sigma2 = rss / dof

    ss_tot = float(((y - y.mean()) ** 2).sum())
    r_squared = 1.0 - rss / ss_tot if ss_tot > 0 else float("nan")

    return OLSFit(
        beta=beta,
        residuals=residuals,
        xtx_inv=xtx_inv,
        rank=rank,
        n=n,
        p=p,
        sigma2=sigma2,
        r_squared=r_squared,
        fitted=fitted,
    )


def sandwich_vcov(
    x: np.ndarray, fit: OLSFit, se_type: SEType
) -> np.ndarray:
    """Bread-meat-bread robust covariance for an OLS fit."""
    if se_type is SEType.CLASSICAL:
        return fit.sigma2 * fit.xtx_inv

    e = fit.residuals
    # X' diag(e_i^2) X, built without materialising the diagonal matrix
    weighted = x * (e[:, None] ** 2)
    meat = x.T @ weighted
    v = fit.xtx_inv @ meat @ fit.xtx_inv
    if se_type is SEType.HC1:
        v = v * (fit.n / (fit.n - fit.p))
    elif se_type is SEType.HC0:
        pass
    else:
        raise EstimationError(
            ErrorCode.CONFIG_ERROR,
            f"se_type {se_type.value!r} is not valid for OLS inference "
            "(use hc0, hc1 or classical)",
        )
    return v
