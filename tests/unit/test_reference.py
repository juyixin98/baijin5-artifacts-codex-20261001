"""Cross-validation of the independent reference regression.

The reference module is fit separately from the estimator kernel. Here we
verify it against raw NumPy formulas written inline in the test, and verify
the kernel's adjusted estimate agrees with the reference treatment coefficient
under the pooled FWL theta.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core import reference
from app.core.config import EstimationConfig
from app.core.contracts import ThetaSource
from app.core import estimator, synthetic


@pytest.mark.unit
def test_reference_ols_matches_raw_numpy_formulas():
    rng = np.random.default_rng(0)
    n = 600
    x = rng.normal(size=n)
    t = rng.integers(0, 2, size=n).astype(float)
    y = 0.5 + 1.7 * t + 2.3 * x + rng.normal(scale=0.4, size=n)

    Z = np.column_stack([np.ones(n), t, x])
    beta = np.linalg.lstsq(Z, y, rcond=None)[0]
    fit = reference.ols_fit(y, Z, hc1=False)

    assert fit["beta"] == pytest.approx([0.5, 1.7, 2.3], abs=0.12)
    assert fit["beta"] == pytest.approx(beta, rel=1e-10)

    # Model SE from the explicit textbook formula.
    resid = y - Z @ beta
    sigma2 = (resid @ resid) / (n - 3)
    expected_se_model = np.sqrt(np.diag(sigma2 * np.linalg.inv(Z.T @ Z)))
    assert fit["se_model"] == pytest.approx(expected_se_model, rel=1e-10)

    # HC1 sandwich from an explicit loop, independent of the module's loop.
    inv = np.linalg.inv(Z.T @ Z)
    meat = sum(float(e) ** 2 * np.outer(z, z) for z, e in zip(Z, resid))
    expected_hc1 = np.sqrt(np.diag(n / (n - 3) * inv @ meat @ inv))
    assert fit["se_hc1"] == pytest.approx(expected_hc1, rel=1e-10)


@pytest.mark.unit
def test_reference_detects_treatment_effect_and_ci():
    rng = np.random.default_rng(1)
    n = 4000
    x = rng.normal(size=n)
    t = rng.integers(0, 2, size=n).astype(float)
    y = -1.0 + 0.8 * t + 1.5 * x + rng.normal(size=n)
    Z = np.column_stack([np.ones(n), t, x])
    fit = reference.ols_fit(y, Z)
    inf = reference.treatment_inference(fit, 1)
    # At this sample size the estimate is essentially exact.
    assert inf["beta_treatment"] == pytest.approx(0.8, abs=0.08)
    # CI arithmetic: symmetric around the estimate with width 2*z*se.
    assert inf["ci_high"] - inf["ci_low"] == pytest.approx(
        2 * 1.959963984540054 * inf["se_treatment"], rel=1e-10
    )
    assert (inf["ci_low"] + inf["ci_high"]) / 2 == pytest.approx(inf["beta_treatment"], rel=1e-10)
    # Significant positive effect.
    assert inf["ci_low"] > 0.0
    assert inf["p_value"] < 1e-6


@pytest.mark.unit
def test_kernel_agrees_with_independent_reference_under_pooled_fwl():
    ds = synthetic.generate("balanced", n=4000, true_effect=2.0, seed=31)
    res = estimator.estimate(
        "exp", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
        [d for d in ds.declarations if d.name != "x_post_leak"],
        EstimationConfig(), "unit-ref", theta_source=ThetaSource.POOLED_FWL,
    )
    # Build the reference design entirely outside the kernel.
    cols = [np.ones(len(ds.outcome)), ds.treatment]
    for name in res.covariates_used:
        v = ds.covariates[name]
        cols.append(v - v.mean())
    Z = np.column_stack(cols)
    fit = reference.ols_fit(ds.outcome, Z, hc1=True)
    inf = reference.treatment_inference(fit, 1)

    assert res.reference_regression["beta_treatment"] == pytest.approx(inf["beta_treatment"], rel=1e-12)
    assert res.reference_regression["se_hc1"] == pytest.approx(inf["se_treatment"], rel=1e-12)
    assert res.adjusted.estimate == pytest.approx(inf["beta_treatment"], abs=1e-9)
    assert abs(inf["beta_treatment"] - 2.0) < 0.1
