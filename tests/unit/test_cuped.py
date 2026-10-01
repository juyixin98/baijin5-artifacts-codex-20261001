"""Unit tests for CUPED adjustment and theta provenance.

Every expected value is derived independently inside the test from raw
arrays (closed-form control-arm OLS, closed-form adjusted outcome), not from
the estimator under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import EstimationConfig
from app.core.contracts import MissingStrategy, ThetaSource, ZeroVarianceStrategy
from app.core.errors import ErrorCode, EstimationError
from app.core import estimator, synthetic


def _adjust(ds, source=ThetaSource.CONTROL, **kw):
    return estimator.estimate(
        "exp", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
        [d for d in ds.declarations if d.name != "x_post_leak"],
        EstimationConfig(), f"unit-cuped-{source.value}",
        theta_source=source, **kw,
    )


def _control_theta_multiple(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    """Closed-form multivariate OLS slopes on the control arm.

    Written independently from the kernel: demean Y and every X column in the
    control arm, then beta = (Xc' Xc)^-1 Xc' Yc.
    """
    Xc = X - X.mean(axis=0)
    yc = y - y.mean()
    return np.linalg.inv(Xc.T @ Xc) @ Xc.T @ yc


def _control_matrix(ds):
    return np.column_stack([ds.covariates["x_pre"], ds.covariates["x_irrelevant"]])


@pytest.mark.unit
def test_theta_recovers_known_coefficient_from_control_arm():
    ds = synthetic.generate("balanced", n=20000, beta_pre=3.0, seed=11)
    res = _adjust(ds, ThetaSource.CONTROL)

    t = ds.treatment
    expected = _control_theta_multiple(ds.outcome[t == 0], _control_matrix(ds)[t == 0])

    assert res.theta.source == "control"
    assert res.theta.n_units_used == int((t == 0).sum())
    assert res.theta.n_arms_used == "control"
    assert res.theta.coefficients["x_pre"] == pytest.approx(expected[0], rel=1e-12)
    assert res.theta.coefficients["x_irrelevant"] == pytest.approx(expected[1], rel=1e-12)
    # Population beta is 3.0; large sample should be close.
    assert abs(res.theta.coefficients["x_pre"] - 3.0) < 0.15
    # Irrelevant covariate coefficient should be near zero.
    assert abs(res.theta.coefficients["x_irrelevant"]) < 0.15


@pytest.mark.unit
def test_theta_never_uses_any_post_treatment_information():
    """control-arm theta is fit on T=0 rows only: prove with row accounting."""
    ds = synthetic.generate("balanced", n=3000, seed=12)
    res = _adjust(ds, ThetaSource.CONTROL)
    assert res.theta.n_units_used == res.n_control
    # Perturbing treated outcomes must not change the fitted theta at all.
    y_perturbed = ds.outcome.copy()
    y_perturbed[ds.treatment == 1] += 500.0
    res2 = estimator.estimate(
        "exp", ds.unit_id, ds.treatment, y_perturbed, ds.covariates,
        [d for d in ds.declarations if d.name != "x_post_leak"],
        EstimationConfig(), "unit-cuped-iso", theta_source=ThetaSource.CONTROL,
    )
    assert res2.theta.coefficients["x_pre"] == pytest.approx(
        res.theta.coefficients["x_pre"], abs=1e-10
    )


@pytest.mark.unit
def test_adjusted_estimate_and_se_match_closed_form():
    ds = synthetic.generate("balanced", n=4000, true_effect=2.0, seed=13)
    res = _adjust(ds)

    t = ds.treatment
    X = _control_matrix(ds)
    theta = _control_theta_multiple(ds.outcome[t == 0], X[t == 0])
    Xc = X - X.mean(axis=0)
    y_adj = ds.outcome - Xc @ theta
    a1, a0 = y_adj[t == 1], y_adj[t == 0]
    expected_est = a1.mean() - a0.mean()
    expected_se = np.sqrt(a1.var(ddof=1) / len(a1) + a0.var(ddof=1) / len(a0))

    assert res.adjusted.estimate == pytest.approx(expected_est, rel=1e-12)
    assert res.adjusted.se == pytest.approx(expected_se, rel=1e-12)
    assert abs(res.adjusted.estimate - 2.0) < 0.1


@pytest.mark.unit
def test_variance_reduction_is_large_for_correlated_covariate():
    ds = synthetic.generate(
        "balanced", n=4000, true_effect=2.0, beta_pre=3.0, noise_sigma=1.0, seed=14
    )
    res = _adjust(ds)
    # Theoretical residual fraction ~ sigma2 / (beta^2 Var(x) + sigma2) = 1/10.
    assert res.variance_reduction > 0.80
    assert res.adjusted.se < res.unadjusted.se * 0.5
    # Point estimate stays unbiased: same known effect within tight tolerance.
    assert abs(res.adjusted.estimate - res.unadjusted.estimate) < 0.1


@pytest.mark.unit
def test_no_variance_reduction_when_covariate_uncorrelated():
    ds = synthetic.generate("no_correlate", n=4000, true_effect=2.0, seed=15)
    res = _adjust(ds)
    # Nothing meaningful to remove: SE ratio must be ~1.
    assert abs(res.adjusted.se - res.unadjusted.se) / res.unadjusted.se < 0.05
    assert abs(res.variance_reduction) < 0.10
    assert abs(res.theta.coefficients["x_pre"]) < 0.12


@pytest.mark.unit
def test_pooled_fwl_theta_matches_ols_treatment_coefficient():
    ds = synthetic.generate("balanced", n=4000, true_effect=2.0, seed=16)
    res = _adjust(ds, ThetaSource.POOLED_FWL)

    # Independent OLS built directly in the test.
    X = np.column_stack([
        np.ones(len(ds.outcome)), ds.treatment,
        ds.covariates["x_pre"] - ds.covariates["x_pre"].mean(),
        ds.covariates["x_irrelevant"] - ds.covariates["x_irrelevant"].mean(),
    ])
    beta = np.linalg.lstsq(X, ds.outcome, rcond=None)[0]
    assert res.theta.coefficients["x_pre"] == pytest.approx(beta[2], rel=1e-10)
    assert res.adjusted.estimate == pytest.approx(beta[1], abs=1e-9)
    assert "ADJUSTED_DIM_VS_OLS_COEFFICIENT_MISMATCH" not in res.warnings


@pytest.mark.unit
def test_missing_values_are_imputed_and_counted():
    ds = synthetic.generate("balanced", n=3000, seed=17, missing_fraction=0.1)
    n_missing = int(np.isnan(ds.covariates["x_pre"]).sum())
    assert n_missing > 100
    res = _adjust(ds, missing_strategy=MissingStrategy.MEAN_IMPUTE)
    diag = {d.name: d for d in res.diagnostics}
    assert diag["x_pre"].n_missing == n_missing
    assert diag["x_pre"].n_imputed == n_missing
    # Imputation of a zero-mean covariate barely moves the estimate.
    assert abs(res.adjusted.estimate - 2.0) < 0.12


@pytest.mark.unit
def test_missing_values_error_strategy_fails_with_category():
    ds = synthetic.generate("balanced", n=2000, seed=18, missing_fraction=0.05)
    with pytest.raises(EstimationError) as exc:
        _adjust(ds, missing_strategy=MissingStrategy.ERROR)
    assert exc.value.code is ErrorCode.COVARIATE_VALUE_MISSING


@pytest.mark.unit
def test_zero_variance_column_is_dropped_with_warning():
    ds = synthetic.generate("balanced", n=2000, seed=19)
    ds.covariates["x_irrelevant"] = np.full(len(ds.outcome), 7.0)
    res = _adjust(ds, zero_variance_strategy=ZeroVarianceStrategy.DROP)
    assert "x_irrelevant" not in res.covariates_used
    diag = {d.name: d for d in res.diagnostics}
    assert diag["x_irrelevant"].dropped is True
    assert diag["x_irrelevant"].zero_variance is True
    assert any("ZERO_VARIANCE" in w for w in res.warnings)
    # Estimation still completes using the informative covariate.
    assert res.status == "completed"
    assert res.variance_reduction > 0.80


@pytest.mark.unit
def test_zero_variance_error_strategy_fails_with_category():
    ds = synthetic.generate("balanced", n=1000, seed=20)
    ds.covariates["x_pre"] = np.zeros(len(ds.outcome))
    with pytest.raises(EstimationError) as exc:
        _adjust(ds, zero_variance_strategy=ZeroVarianceStrategy.ERROR)
    assert exc.value.code is ErrorCode.ZERO_VARIANCE_COVARIATE


@pytest.mark.unit
def test_leaked_covariate_is_rejected():
    ds = synthetic.generate("leakage", n=3000, seed=21)
    with pytest.raises(EstimationError) as exc:
        estimator.estimate(
            "exp", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
            ds.declarations,  # includes x_post_leak with pre_treatment=False
            EstimationConfig(), "unit-leak",
        )
    assert exc.value.code is ErrorCode.LEAKED_COVARIATE
    assert "x_post_leak" in exc.value.details["covariates"]
