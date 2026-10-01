"""Unit tests for the unadjusted estimator.

Reference quantities are recomputed from raw arrays inside the test; they do
not come from the module under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.core.config import EstimationConfig
from app.core.contracts import ThetaSource
from app.core import estimator, synthetic


def _run(ds, **kw):
    return estimator.estimate(
        "exp", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
        ds.declarations, EstimationConfig(), "unit-unadj", **kw,
    )


@pytest.mark.unit
def test_unadjusted_matches_independent_group_means():
    ds = synthetic.generate("balanced", n=1500, seed=42)
    y, t = ds.outcome, ds.treatment

    res = _run(ds)

    y1, y0 = y[t == 1], y[t == 0]
    expected_est = y1.mean() - y0.mean()
    expected_se = np.sqrt(y1.var(ddof=1) / len(y1) + y0.var(ddof=1) / len(y0))

    assert res.unadjusted.estimate == pytest.approx(expected_est, rel=1e-12)
    assert res.unadjusted.se == pytest.approx(expected_se, rel=1e-12)
    assert res.unadjusted.n_treatment == int((t == 1).sum())
    assert res.unadjusted.n_control == int((t == 0).sum())


@pytest.mark.unit
def test_unadjusted_reports_side_by_side_and_group_consistency():
    ds = synthetic.generate("balanced", n=1000, seed=7)
    res = _run(ds)
    assert res.status == "completed"
    assert res.adjusted is not None
    # Same grouping design on both estimates.
    assert res.adjusted.n_treatment == res.unadjusted.n_treatment
    assert res.adjusted.n_control == res.unadjusted.n_control
    assert res.adjusted.kind == "cuped_adjusted"


@pytest.mark.unit
def test_known_effect_unadjusted_coverage_across_replicates():
    """Nominal 95% CI coverage of the unadjusted estimator over 300 seeds."""
    true_effect = 1.5
    covered = 0
    reps = 300
    for seed in range(reps):
        ds = synthetic.generate("balanced", n=800, true_effect=true_effect, seed=10_000 + seed)
        res = _run(ds)
        e = res.unadjusted
        if e.ci_low <= true_effect <= e.ci_high:
            covered += 1
    coverage = covered / reps
    # Expected 0.95; allow Monte Carlo +/- 0.05.
    assert 0.90 <= coverage <= 0.995, coverage
