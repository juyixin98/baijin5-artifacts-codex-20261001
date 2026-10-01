"""Monte-Carlo statistical property checks.

These verify frequentist *behaviour* across hundreds of synthetic
experiments with known truth: bias, empirical vs nominal SE, CI coverage and
variance reduction. Marked ``mcsim`` because they are slower.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.contracts import SEType, ThetaSource
from app.core.data import PreparedData
from app.core.estimators import cuped, estimate_theta, group_difference


def _make_prepared(y, t, x, names=("x",)) -> PreparedData:
    x = np.atleast_2d(x).T if x.ndim == 1 else np.asarray(x, dtype=float)
    return PreparedData(
        y=np.asarray(y, dtype=float),
        t=np.asarray(t, dtype=float),
        x=x,
        covariate_names=tuple(names[: x.shape[1]]),
        n_rows=len(y), n_complete_rows=len(y), n_dropped_rows=0,
        dropped_covariates=(),
        dropped_covariate_missing_counts=(),
        covariate_missing_counts=tuple([0] * x.shape[1]),
        outcome_missing_count=0, constant_columns=(), warnings=(),
    )


def _simulate(s: int, n: int, tau: float, beta, sigma_eps: float,
              p: float = 0.5, seed: int = 0, x_builder=None,
              extra_x=None) -> list[PreparedData]:
    rng = np.random.default_rng(seed)
    runs = []
    beta = np.atleast_1d(beta).astype(float)
    for _ in range(s):
        if x_builder is None:
            x = rng.normal(0.0, 1.0, size=(n, max(len(beta), 1)))
        else:
            x = x_builder(rng, n)
        t = (rng.uniform(size=n) < p).astype(float)
        eps = rng.normal(0.0, sigma_eps, n)
        y = 0.5 + tau * t + x @ beta + eps
        if extra_x is not None:
            x = np.column_stack([x, extra_x(rng, y, t, n)])
        runs.append(_make_prepared(y, t, x))
    return runs


def _ate_series(runs, *, adjusted: bool):
    estimates, ses = [], []
    for d in runs:
        if adjusted:
            theta_fit = estimate_theta(d, ThetaSource.CONTROL_PRE)
            est = cuped(d, theta_fit, SEType.WELCH)
        else:
            est = group_difference(d, SEType.WELCH)
        estimates.append(est.estimate)
        ses.append(est.se)
    return np.array(estimates), np.array(ses)


@pytest.mark.mcsim
def test_cuped_is_unbiased_and_se_matches_empirical(case_log):
    s, n, tau = 500, 400, 1.0
    runs = _simulate(s, n, tau=tau, beta=2.0, sigma_eps=1.0, seed=11)
    est, se = _ate_series(runs, adjusted=True)
    un_est, un_se = _ate_series(runs, adjusted=False)

    emp_se = est.std(ddof=1)
    bias = est.mean() - tau
    case_log("MC bias/SE", s=s, n=n, bias=float(bias),
             empirical_se=float(emp_se), mean_analytic_se=float(se.mean()),
             unadjusted_se=float(un_est.std(ddof=1)))

    assert abs(bias) < 0.04
    # Analytic Welch SE tracks the empirical sampling SD.
    assert 0.92 < float(se.mean() / emp_se) < 1.08
    # Theoretical reduction: rho^2 = 4/(4+1) = 0.8 -> variance ratio ~0.2.
    var_ratio = float(est.var(ddof=1) / un_est.var(ddof=1))
    assert 0.15 < var_ratio < 0.26


@pytest.mark.mcsim
def test_ci95_coverage_for_unadjusted_and_cuped(case_log):
    s, n, tau = 600, 500, 0.75
    runs = _simulate(s, n, tau=tau, beta=2.5, sigma_eps=1.0, seed=23)
    cover = {}
    for label, adjusted in (("unadjusted", False), ("cuped", True)):
        hits = 0
        for d in runs:
            if adjusted:
                theta_fit = estimate_theta(d, ThetaSource.CONTROL_PRE)
                est = cuped(d, theta_fit, SEType.WELCH)
            else:
                est = group_difference(d, SEType.WELCH)
            if est.ci_low <= tau <= est.ci_high:
                hits += 1
        cover[label] = hits / s
    case_log("MC coverage", s=s, n=n, **cover)
    assert 0.92 <= cover["unadjusted"] <= 0.98
    assert 0.92 <= cover["cuped"] <= 0.98


@pytest.mark.mcsim
def test_uncorrelated_covariate_neither_reduces_variance_nor_biases():
    s, n, tau = 400, 400, 1.0
    runs = _simulate(s, n, tau=tau, beta=0.0, sigma_eps=2.0, seed=37)
    est, _ = _ate_series(runs, adjusted=True)
    un_est, _ = _ate_series(runs, adjusted=False)
    ratio = float(est.var(ddof=1) / un_est.var(ddof=1))
    assert 0.90 < ratio < 1.12
    assert abs(est.mean() - tau) < 0.08


@pytest.mark.mcsim
def test_arm_imbalance_does_not_bias_cuped_but_biases_unadjusted_on_baseline(case_log):
    """With correlated X and 30/70 allocation, unadjusted suffers baseline
    imbalance noise while CUPED stays centred on truth."""
    s, n, tau = 500, 500, 1.0
    runs = _simulate(s, n, tau=tau, beta=3.0, sigma_eps=1.0, p=0.30, seed=51)
    est, _ = _ate_series(runs, adjusted=True)
    un_est, _ = _ate_series(runs, adjusted=False)
    case_log("imbalanced allocation", p=0.30,
             cuped_bias=float(est.mean() - tau),
             unadjusted_bias=float(un_est.mean() - tau),
             cuped_sd=float(est.std()), unadjusted_sd=float(un_est.std()))
    assert abs(est.mean() - tau) < 0.06
    # Adjustment still removes the covariate variance even at 30/70 split.
    assert est.std() < un_est.std() / 2.5


@pytest.mark.mcsim
def test_leaking_post_treatment_covariate_biases_when_force_included(case_log):
    """A post-treatment field Z = Y + noise mechanically shifts the estimate;
    the backend excludes it under 'flag', but 'ignore' must show the damage."""
    s, n, tau = 300, 400, 1.0
    rng = np.random.default_rng(71)
    biased_estimates = []
    for _ in range(s):
        x = rng.normal(0, 1, n)
        t = (rng.uniform(size=n) < 0.5).astype(float)
        eps = rng.normal(0, 1, n)
        y = 0.5 + tau * t + 0.8 * x + eps
        z = y + 0.5 * rng.normal(0, 1, n)  # post-treatment, arm-imbalanced
        d_leak = _make_prepared(y, t, z, names=("z",))
        theta_fit = estimate_theta(d_leak, ThetaSource.POOLED_PRE)
        est = cuped(d_leak, theta_fit, SEType.WELCH)
        biased_estimates.append(est.estimate)
    mean_biased = float(np.mean(biased_estimates))
    case_log("leakage bias", mean_with_leak=mean_biased, truth=tau)
    # Forcing the post-treatment field into theta shrinks the apparent effect
    # substantially toward zero: demonstrable, signed bias.
    assert abs(mean_biased - tau) > 0.2
    assert abs(mean_biased) < abs(tau)
