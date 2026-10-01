"""Estimation kernel.

Three estimators share one contract:

* :func:`group_difference` — unadjusted difference of arm means with Welch or
  pooled-variance standard errors, matching an independent two-group design.
* :func:`cuped` — Deng et al. (2013) CUPED: the adjustment coefficient theta
  is estimated from an explicitly declared source (control-arm pre-treatment
  regression by default), the adjusted outcome is
  ``y - (X - xbar) @ theta`` and the ATE is the difference of adjusted arm
  means, with SE computed on adjusted outcomes per arm.
* :func:`lin_ancova` — OLS ANCOVA. With interactions this is the Lin (2013)
  residualised estimator ``y = a + tau T + gamma'Xc + delta'(T*Xc)`` with
  heteroskedasticity-robust HC1 standard errors; without interactions it is
  the pooled-slope ANCOVA whose slope is the within-arm regression of y on X.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from .contracts import (
    ErrorCode,
    Estimate,
    EstimationError,
    Estimator,
    SEType,
    ThetaSource,
)
from .data import PreparedData
from .ols import ols, sandwich_vcov

EPS = 1e-12


def _welch(v1: float, n1: int, v0: float, n0: int) -> tuple[float, float]:
    """Welch SE and Satterthwaite df from the same (non-negative) terms."""
    a = max(v1, 0.0) / n1
    b = max(v0, 0.0) / n0
    se2 = a + b
    df = se2 ** 2 / (a ** 2 / (n1 - 1) + b ** 2 / (n0 - 1))
    return float(np.sqrt(se2)), float(df)


# --------------------------------------------------------------------------- #
# Unadjusted comparison
# --------------------------------------------------------------------------- #
def group_difference(data: PreparedData, se_type: SEType,
                     ci_level: float = 0.95) -> Estimate:
    y, t = data.y, data.t
    y1, y0 = y[t == 1], y[t == 0]
    n1, n0 = len(y1), len(y0)
    if n1 < 2 or n0 < 2:
        raise EstimationError(
            ErrorCode.INSUFFICIENT_SAMPLE,
            f"each arm needs >= 2 observations for a variance; "
            f"n_treatment={n1}, n_control={n0}",
        )
    m1, m0 = float(y1.mean()), float(y0.mean())
    v1, v0 = float(y1.var(ddof=1)), float(y0.var(ddof=1))

    ate = m1 - m0
    if se_type is SEType.WELCH:
        se, df = _welch(v1, n1, v0, n0)
    elif se_type is SEType.POOLED:
        sp2 = ((n1 - 1) * v1 + (n0 - 1) * v0) / (n1 + n0 - 2)
        se = float(np.sqrt(sp2 * (1.0 / n1 + 1.0 / n0)))
        df = float(n1 + n0 - 2)
    else:
        raise EstimationError(
            ErrorCode.CONFIG_ERROR,
            f"se_type {se_type.value!r} invalid for unadjusted comparison "
            "(use 'welch' or 'pooled')",
        )
    return _pack_estimate(
        estimator=Estimator.GROUPS, ate=ate, se=se, df=df,
        n1=n1, n0=n0, se_type=se_type, ci_level=ci_level,
        residual_variance=float((((n1 - 1) * v1 + (n0 - 1) * v0)) / (n1 + n0 - 2)),
    )


# --------------------------------------------------------------------------- #
# CUPED
# --------------------------------------------------------------------------- #
def _standardized_condition(x_block: np.ndarray) -> float:
    """Condition number of the z-scored covariate block (scale invariant).

    Columns are centred and divided by their sample SD (constant columns are
    excluded earlier), so the reported number reflects multicollinearity, not
    the units in which a covariate happens to be measured.
    """
    mu = x_block.mean(axis=0, keepdims=True)
    sd = x_block.std(axis=0, ddof=1, keepdims=True)
    z = (x_block - mu) / np.where(sd > 0, sd, 1.0)
    return float(np.linalg.cond(z))


@dataclass(frozen=True)
class ThetaFit:
    theta: np.ndarray
    se: np.ndarray | None       # None when source='given' (external coefficient)
    source: ThetaSource
    r_squared: float | None
    condition_number: float | None
    n_used: int


def estimate_theta(
    data: PreparedData,
    source: ThetaSource,
    given: np.ndarray | None = None,
) -> ThetaFit:
    """Estimate the CUPED coefficient from the declared source.

    The covariate is required to be pre-treatment by the API contract; the
    *source* only controls which rows the slope is fitted on:

    * ``control_pre`` — control arm only (Deng et al. 2013 recommendation),
    * ``pooled_pre``  — both arms, residualising the treatment indicator
      (equivalent to the pooled within-arm slope used by ANCOVA),
    * ``given``       — caller-supplied vector, no fitting.
    """
    x, y, t = data.x, data.y, data.t
    k = x.shape[1]
    if k == 0:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            "CUPED requires at least one covariate",
        )

    if source is ThetaSource.GIVEN:
        if given is None or len(given) != k:
            raise EstimationError(
                ErrorCode.CONFIG_ERROR,
                f"theta_source='given' requires a theta vector of length {k}",
            )
        theta = np.asarray(given, dtype=np.float64)
        if not np.all(np.isfinite(theta)):
            raise EstimationError(ErrorCode.CONFIG_ERROR, "given theta contains non-finite values")
        return ThetaFit(theta=theta, se=None, source=source,
                        r_squared=None, condition_number=None,
                        n_used=0)

    if source is ThetaSource.CONTROL_PRE:
        rows = t == 0
        design = np.column_stack([np.ones(int(rows.sum())), x[rows]])
        fit = ols(design, y[rows], label="theta[control_pre]")
        n_used = int(rows.sum())
        x_block = x[rows]
    elif source is ThetaSource.POOLED_PRE:
        # y ~ T + X: slope on X is the pooled within-arm (ANCOVA) slope and
        # stays orthogonal to assignment by construction.
        design = np.column_stack([np.ones(len(y)), t, x])
        fit = ols(design, y, label="theta[pooled_pre]")
        n_used = len(y)
        x_block = x
    else:  # pragma: no cover - exhaustive enum
        raise EstimationError(ErrorCode.CONFIG_ERROR, f"unknown theta source {source}")

    cond = _standardized_condition(x_block)
    if source is ThetaSource.POOLED_PRE:
        theta = fit.beta[2:]
        se = np.sqrt(np.diag(fit.sigma2 * fit.xtx_inv))[2:]
    else:
        theta = fit.beta[1:]
        se = np.sqrt(np.diag(fit.sigma2 * fit.xtx_inv))[1:]
    return ThetaFit(theta=theta, se=se, source=source,
                    r_squared=float(fit.r_squared),
                    condition_number=cond, n_used=n_used)


def cuped(
    data: PreparedData,
    theta_fit: ThetaFit,
    se_type: SEType,
    ci_level: float = 0.95,
) -> Estimate:
    """Adjusted difference of means with Welch/pooled inference."""
    y, t, x = data.y, data.t, data.x
    if se_type not in (SEType.WELCH, SEType.POOLED):
        raise EstimationError(
            ErrorCode.CONFIG_ERROR,
            f"se_type {se_type.value!r} invalid for CUPED "
            "(use 'welch' or 'pooled'; use lin_ancova for robust OLS SE)",
        )
    x_centered = x - x.mean(axis=0, keepdims=True)
    y_adj = y - x_centered @ theta_fit.theta

    y1, y0 = y_adj[t == 1], y_adj[t == 0]
    n1, n0 = len(y1), len(y0)
    m1, m0 = float(y1.mean()), float(y0.mean())
    v1, v0 = float(y1.var(ddof=1)), float(y0.var(ddof=1))
    ate = m1 - m0

    if se_type is SEType.WELCH:
        se, df = _welch(v1, n1, v0, n0)
    else:
        sp2 = ((n1 - 1) * v1 + (n0 - 1) * v0) / (n1 + n0 - 2)
        se = float(np.sqrt(max(sp2, 0.0) * (1.0 / n1 + 1.0 / n0)))
        df = float(n1 + n0 - 2)

    return _pack_estimate(
        estimator=Estimator.CUPED, ate=ate, se=se, df=df,
        n1=n1, n0=n0, se_type=se_type, ci_level=ci_level,
        residual_variance=float(((n1 - 1) * v1 + (n0 - 1) * v0) / (n1 + n0 - 2)),
        theta=tuple(float(v) for v in theta_fit.theta),
        theta_se=(None if theta_fit.se is None
                  else tuple(float(v) for v in theta_fit.se)),
        theta_source=theta_fit.source.value,
        covariate_names=data.covariate_names,
        extra={
            "theta_r_squared": theta_fit.r_squared,
            "theta_condition_number": theta_fit.condition_number,
            "theta_n_used": theta_fit.n_used,
            "adjusted_var_treatment": v1,
            "adjusted_var_control": v0,
        },
    )


# --------------------------------------------------------------------------- #
# Regression ANCOVA / Lin (2013)
# --------------------------------------------------------------------------- #
def lin_ancova(
    data: PreparedData,
    interactions: bool,
    se_type: SEType,
    ci_level: float = 0.95,
) -> Estimate:
    """OLS treatment effect with pre-treatment covariates.

    Covariates are centred at the pooled sample mean (Lin 2013). With
    ``interactions=True`` the design is ``[1, T, Xc, T*Xc]`` and the
    coefficient on T is the interaction-adjusted ATE with HC1 robust SE by
    default; with ``interactions=False`` it is ``[1, T, Xc]``.
    """
    y, t, x = data.y, data.t, data.x
    k = x.shape[1]
    if k == 0:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            "lin_ancova requires at least one covariate",
        )
    xc = x - x.mean(axis=0, keepdims=True)
    cols = [np.ones(len(y)), t, xc]
    if interactions:
        cols.append(t[:, None] * xc)
    design = np.column_stack(cols)

    fit = ols(design, y, label="lin_ancova")
    if se_type in (SEType.HC0, SEType.HC1, SEType.CLASSICAL):
        vcov = sandwich_vcov(design, fit, se_type)
    elif se_type is SEType.WELCH:
        # Robust sandwich is the OLS analogue of Welch; honour the requested
        # family explicitly rather than silently switching.
        raise EstimationError(
            ErrorCode.CONFIG_ERROR,
            "se_type='welch' belongs to the CUPED/group estimator; "
            "regression inference uses 'hc1' (default), 'hc0' or 'classical'",
        )
    else:  # pragma: no cover - exhaustive enum
        raise EstimationError(ErrorCode.CONFIG_ERROR, f"unsupported se_type {se_type}")

    ate = float(fit.beta[1])
    se = float(np.sqrt(max(vcov[1, 1], 0.0)))
    df = float(fit.n - fit.p)
    return _pack_estimate(
        estimator=Estimator.LIN, ate=ate, se=se, df=df,
        n1=int((t == 1).sum()), n0=int((t == 0).sum()),
        se_type=se_type, ci_level=ci_level,
        residual_variance=float(fit.sigma2), r_squared=float(fit.r_squared),
        covariate_names=data.covariate_names,
        extra={
            "interactions": bool(interactions),
            "coefficients": [float(v) for v in fit.beta],
            "coefficient_names": (
                ["intercept", "treatment"]
                + [f"cov:{n}" for n in data.covariate_names]
                + [f"treatment_x_cov:{n}" for n in data.covariate_names]
                if interactions else
                ["intercept", "treatment"] + [f"cov:{n}" for n in data.covariate_names]
            ),
        },
    )


# --------------------------------------------------------------------------- #
# shared packing
# --------------------------------------------------------------------------- #
def _pack_estimate(*, estimator: Estimator, ate: float, se: float, df: float,
                   n1: int, n0: int, se_type: SEType, ci_level: float,
                   residual_variance: float | None = None,
                   r_squared: float | None = None,
                   theta: tuple[float, ...] | None = None,
                   theta_se: tuple[float, ...] | None = None,
                   theta_source: str | None = None,
                   covariate_names: tuple[str, ...] | None = None,
                   extra: dict | None = None) -> Estimate:
    if not np.isfinite(se) or se <= EPS:
        raise EstimationError(
            ErrorCode.ZERO_VARIANCE_OUTCOME,
            f"degenerate standard error ({se}) for {estimator.value}; "
            "adjusted outcome carries no variance",
            {"se": se},
        )
    t_stat = float(ate / se)
    p_value = float(2.0 * stats.t.sf(abs(t_stat), df))
    crit = float(stats.t.ppf(0.5 + ci_level / 2.0, df))
    return Estimate(
        estimator=estimator.value,
        estimate=float(ate),
        se=float(se),
        ci_low=float(ate - crit * se),
        ci_high=float(ate + crit * se),
        t_stat=t_stat,
        p_value=p_value,
        df=float(df),
        n_treatment=n1,
        n_control=n0,
        se_type=se_type.value,
        residual_variance=residual_variance,
        r_squared=r_squared,
        theta=theta,
        theta_se=theta_se,
        theta_source=theta_source,
        covariate_names=covariate_names,
        extra=extra or {},
    )
