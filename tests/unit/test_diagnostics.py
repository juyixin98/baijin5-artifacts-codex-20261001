"""Unit tests for evidence/diagnostic outputs and the imbalance scenario."""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import EstimationConfig
from app.core import estimator, synthetic


def _adjust(ds, **kw):
    return estimator.estimate(
        "exp", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
        [d for d in ds.declarations if d.name != "x_post_leak"],
        EstimationConfig(), "unit-diag", **kw,
    )


@pytest.mark.unit
def test_imbalanced_realization_is_flagged_and_adjustment_recovers_effect():
    # Moderate n is deliberate: a realized SMD >= 0.30 under true 50/50
    # randomization is astronomically unlikely at n=4000 but readily sampled
    # at n=500 (SE of the SMD ~ 2/sqrt(n)).
    ds = synthetic.generate(
        "imbalanced", n=500, true_effect=1.0, beta_pre=3.0, seed=23, imbalance_smd=0.30
    )
    res = _adjust(ds)
    diag = {d.name: d for d in res.diagnostics}

    # The realized draw must actually be imbalanced (scenario self-check).
    assert abs(diag["x_pre"].standardized_mean_diff) >= 0.25
    # Under covariate imbalance, unadjusted point estimate is shifted by the
    # chance imbalance; CUPED adjustment must sit materially closer to truth.
    unadj_err = abs(res.unadjusted.estimate - ds.true_effect)
    adj_err = abs(res.adjusted.estimate - ds.true_effect)
    assert adj_err < unadj_err
    assert adj_err < 0.25
    # Balance evidence fields are populated.
    assert np.isfinite(diag["x_pre"].balance_z)
    assert 0.0 <= diag["x_pre"].balance_p_value <= 1.0


@pytest.mark.unit
def test_diagnostics_report_control_arm_outcome_correlation():
    ds = synthetic.generate("balanced", n=3000, seed=24)
    res = _adjust(ds)
    diag = {d.name: d for d in res.diagnostics}
    # beta=3, x variance 1, noise 1 -> corr ~ 3/sqrt(10) ~ 0.949 in control arm.
    assert diag["x_pre"].corr_outcome_control == pytest.approx(0.949, abs=0.05)
    assert abs(diag["x_irrelevant"].corr_outcome_control) < 0.15


@pytest.mark.unit
def test_result_carries_versions_and_run_identity():
    ds = synthetic.generate("balanced", n=800, seed=25)
    res = _adjust(ds)
    assert res.run_id == "unit-diag"
    assert set(res.versions) == {"app", "numpy", "scipy"}
    assert res.versions["app"]
    assert res.versions["numpy"] == np.__version__


@pytest.mark.unit
def test_side_by_side_payload_structure():
    ds = synthetic.generate("balanced", n=800, seed=26)
    payload = _adjust(ds).to_dict()
    assert payload["unadjusted"]["kind"] == "unadjusted"
    assert payload["adjusted"]["kind"] == "cuped_adjusted"
    assert payload["theta"]["source"] == "control"
    assert payload["reference_regression"]["model"].startswith("y ~")
    assert set(payload["diagnostics"][0]) >= {
        "name", "n_missing", "n_imputed", "variance", "zero_variance",
        "dropped", "standardized_mean_diff", "balance_p_value",
        "leakage_suspected", "corr_outcome_control",
    }
