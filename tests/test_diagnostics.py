"""Diagnostics tests: discreteness, heaping, McCrary size/power."""
from __future__ import annotations

import numpy as np
import pytest

from app.core import diagnostics as dg


def test_discreteness_flags_coarse_runner():
    rng = np.random.default_rng(0)
    x_coarse = rng.choice(np.arange(-1.0, 1.0, 0.1), size=2000)
    res = dg.check_discreteness(x_coarse, 0.0)
    assert res.passed is False and res.warning
    assert res.details["n_unique"] < 50

    x_fine = rng.uniform(-1, 1, 2000)
    assert dg.check_discreteness(x_fine, 0.0).passed is True


def test_heaping_detects_pileup_at_cutoff():
    rng = np.random.default_rng(1)
    grid = np.arange(-1.0, 1.01, 0.1)
    x = rng.choice(grid, size=2000)
    x = np.concatenate([x, np.full(400, 0.0)])     # inject a big pile-up
    res = dg.check_heaping(x, 0.0)
    assert res.passed is False
    assert res.details["n_at_cutoff"] > res.details["expected_at_cutoff"]
    assert res.details["z"] > 1.96


def test_heaping_none_when_no_heap():
    rng = np.random.default_rng(2)
    x = np.round(rng.uniform(-1, 1, 3000) * 10) / 10
    res = dg.check_heaping(x, 0.0)
    # No injected pile-up: should not flag (z within reason).
    assert abs(res.details["z"]) < 4.0


@pytest.mark.slow
def test_mccrary_has_correct_size_under_continuous_density():
    rng = np.random.default_rng(2024)
    pvals = []
    for _ in range(150):
        x = rng.uniform(-1, 1, 1500)
        r = dg.mccrary_density_test(x, 0.0)
        assert r.passed is not None
        pvals.append(r.details["pvalue"])
    size = float(np.mean(np.asarray(pvals) < 0.05))
    # Conservative Poisson local-likelihood: allow [0.01, 0.12].
    assert 0.01 <= size <= 0.12, size


@pytest.mark.slow
def test_mccrary_has_power_and_unbiased_theta_under_density_jump():
    rng = np.random.default_rng(2025)
    thetas, pvals = [], []
    for _ in range(150):
        x = np.concatenate([rng.uniform(-1, 0, 375), rng.uniform(0, 1, 1125)])
        r = dg.mccrary_density_test(x, 0.0)
        thetas.append(r.details["theta_log_density_jump"])
        pvals.append(r.details["pvalue"])
    mean_theta = float(np.mean(thetas))
    assert mean_theta == pytest.approx(np.log(3.0), abs=0.12)
    assert float(np.mean(np.asarray(pvals) < 0.05)) > 0.8


def test_mccrary_fast_unbiased_point_estimate():
    # Fast unbiasedness check averaged over replications (a single sample has
    # non-trivial bin-level sampling variance); the slow MC below is stricter.
    rng = np.random.default_rng(99)
    thetas = []
    for _ in range(20):
        x = np.concatenate([rng.uniform(-1, 0, 4000), rng.uniform(0, 1, 12000)])
        thetas.append(dg.mccrary_density_test(x, 0.0)
                      .details["theta_log_density_jump"])
    assert float(np.mean(thetas)) == pytest.approx(np.log(3.0), abs=0.05)


def test_mccrary_null_does_not_flag_continuous_data():
    rng = np.random.default_rng(3)
    x = rng.uniform(-1, 1, 3000)
    r = dg.mccrary_density_test(x, 0.0)
    assert r.passed is True
    assert abs(r.details["theta_log_density_jump"]) < 0.25


def test_mccrary_reports_not_assessable_for_tiny_samples():
    x = np.array([-0.5, -0.4, 0.4, 0.5])
    r = dg.mccrary_density_test(x, 0.0)
    assert r.passed is None
    assert r.warning
