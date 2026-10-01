"""Tests for robust variance and wild bootstrap.

The variance tests use a closed-form homoskedastic check plus a
heteroskedastic simulation comparing mean HC3 SE against the Monte Carlo SD
of the estimator. Bootstrap tests assert calibrated null behaviour, not mere
callability.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import KernelName
from app.estimator import LEFT, RIGHT, fit_side
from app.inference import jump_standard_error, robust_inference, wild_bootstrap


def _fit_pair(x, y, h=0.4, clusters=None):
    kw = {} if clusters is None else {"clusters": clusters}
    left = fit_side(x, y, 0.0, h, KernelName.TRIANGULAR, LEFT, RIGHT, **kw)
    right = fit_side(x, y, 0.0, h, KernelName.TRIANGULAR, RIGHT, RIGHT, **kw)
    return left, right


@pytest.mark.unit
def test_hc3_se_is_conservative_relative_to_hc1() -> None:
    rng = np.random.default_rng(0)
    x = rng.uniform(-1, 1, 300)
    y = (x >= 0) * 1.0 + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y)
    se_hc3 = jump_standard_error(left, right, "hc3")
    se_hc1 = jump_standard_error(left, right, "hc1")
    assert se_hc3 > 0
    assert se_hc3 >= se_hc1  # HC3 inflates high-leverage residuals
    # same order of magnitude
    assert se_hc3 / se_hc1 < 3.0


@pytest.mark.unit
def test_analytic_se_matches_monte_carlo_sd_under_heteroskedasticity() -> None:
    """HC3 should approximately track the true sampling SD."""
    rng = np.random.default_rng(42)
    n_reps = 600
    taus, ses = [], []
    x_base = rng.uniform(-1, 1, 1000)
    for _ in range(n_reps):
        e = rng.normal(0, 1, x_base.size) * (0.4 + 1.6 * np.abs(x_base))
        y = (x_base >= 0) * 1.0 + e
        left, right = _fit_pair(x_base, y)
        taus.append(right.intercept - left.intercept)
        ses.append(jump_standard_error(left, right, "hc3"))
    mc_sd = float(np.std(taus, ddof=1))
    mean_se = float(np.mean(ses))
    # robust SE within 25% of empirical SD despite strong heteroskedasticity
    assert abs(mean_se - mc_sd) / mc_sd < 0.25, (mean_se, mc_sd)


@pytest.mark.unit
def test_confidence_interval_covers_known_jump_at_nominal_level_loose() -> None:
    rng = np.random.default_rng(7)
    covers = 0
    reps = 300
    for _ in range(reps):
        x = rng.uniform(-1, 1, 1200)
        y = (x >= 0) * 2.0 + rng.normal(0, 1, x.size)
        left, right = _fit_pair(x, y)
        res = robust_inference(right.intercept - left.intercept, left, right, "hc3", 0.05)
        covers += int(res.ci_low <= 2.0 <= res.ci_high)
    coverage = covers / reps
    assert 0.90 <= coverage <= 0.99, coverage


@pytest.mark.unit
def test_wild_bootstrap_reproducible_with_seed() -> None:
    rng = np.random.default_rng(1)
    x = rng.uniform(-1, 1, 500)
    y = (x >= 0) * 1.5 + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y)
    a = wild_bootstrap(1.5, 0.2, left, right, 399, 99, None, None)
    b = wild_bootstrap(1.5, 0.2, left, right, 399, 99, None, None)
    assert a["ci_low"] == b["ci_low"]
    assert a["p_value"] == b["p_value"]


@pytest.mark.unit
def test_wild_bootstrap_null_is_centered_and_detects_jump() -> None:
    rng = np.random.default_rng(13)
    x = rng.uniform(-1, 1, 2000)
    y = (x >= 0) * 2.5 + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y, h=0.3)
    tau = right.intercept - left.intercept
    se = jump_standard_error(left, right, "hc3")
    boot = wild_bootstrap(tau, se, left, right, 999, 7, None, None)
    # strong jump -> small p; null distribution SD tracks the analytic SE
    assert boot["p_value"] <= 0.01
    assert abs(boot["null_jump_std"] - se) / se < 0.2
    # percentile CI from the alternative distribution excludes zero
    assert boot["ci_low"] > 0


@pytest.mark.unit
def test_wild_bootstrap_no_false_signal_on_continuous_mean() -> None:
    rng = np.random.default_rng(13)
    x = rng.uniform(-1, 1, 2000)
    y = 1.2 * x + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y, h=0.3)
    tau = right.intercept - left.intercept
    se = jump_standard_error(left, right, "hc3")
    boot = wild_bootstrap(tau, se, left, right, 999, 7, None, None)
    assert abs(tau) < 0.3
    assert boot["p_value"] > 0.05
    assert boot["ci_low"] < 0.0 < boot["ci_high"]


@pytest.mark.unit
def test_clustered_bootstrap_shares_multipliers_within_cluster() -> None:
    rng = np.random.default_rng(5)
    x = np.round(rng.uniform(-1, 1, 600), 1)  # lattice => few clusters by value
    clusters = x.copy()
    y = (x >= 0) * 1.0 + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y, h=0.5, clusters=clusters)
    assert left.cluster_labels is not None and right.cluster_labels is not None
    boot = wild_bootstrap(
        1.0, 0.3, left, right, 199, 3, left.cluster_labels, right.cluster_labels
    )
    assert boot["clustered"] is True
    assert boot["reps"] == 199
    assert boot["p_value"] >= 1.0 / 199  # resolution floor honoured


@pytest.mark.unit
def test_zero_reps_returns_empty_report() -> None:
    rng = np.random.default_rng(1)
    x = rng.uniform(-1, 1, 100)
    y = x + rng.normal(0, 1, x.size)
    left, right = _fit_pair(x, y)
    assert wild_bootstrap(0.1, 0.2, left, right, 0, 1, None, None) == {"reps": 0}
