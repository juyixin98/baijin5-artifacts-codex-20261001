"""End-to-end estimator tests against independent reference oracles.

These assert *concrete numerical results and explicit failure categories*,
never just "the endpoint runs". Reference answers come from
``tests/reference.py`` (hand-written normal equations / np.polyfit /
linregress), which imports nothing from the estimator under test.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.contracts import FailureCategory, RunStatus
from app.core.estimator import rd_estimate
from app.dgp import (
    density_discontinuity,
    heaped_discrete,
    no_jump,
    sharp_jump,
    sparse_boundary,
)
from tests.reference import (
    linregress_uniform_boundary,
    polyfit_boundary_intercept,
    rd_reference,
)


# --------------------------------------------------------------------------- #
# Point estimation against the independent reference
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("kernel", ["triangular", "epanechnikov", "uniform", "tricube"])
@pytest.mark.parametrize("h", [0.15, 0.25, 0.40])
def test_estimate_matches_independent_reference_coefficients(kernel, h):
    d = sharp_jump(n=4000, tau=7.0, slope=1.5, curvature=0.8, noise=1.0, seed=11)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, kernel=kernel, bandwidth=h,
                      se_type="hc1", run_id=f"ref-{kernel}-{h}")
    ref = rd_reference(d.x, d.y, cutoff=d.cutoff, h=h, kernel=kernel)
    assert res.status is not RunStatus.FAILED, res.failure_reason
    # Coefficients: two independent linear-algebra paths must agree closely.
    assert res.left.intercept == pytest.approx(ref.intercept_left, abs=1e-7)
    assert res.right.intercept == pytest.approx(ref.intercept_right, abs=1e-7)
    assert res.tau == pytest.approx(ref.tau, abs=1e-8)
    # Robust SEs come from different sandwich flavours (HC2 vs HC1); they
    # must be close but are not expected bit-identical.
    assert res.se == pytest.approx(ref.se_tau, rel=0.06)


def test_estimate_matches_polyfit_and_linregress_paths():
    d = sharp_jump(n=4000, tau=3.0, seed=12)
    h = 0.3
    res = rd_estimate(d.x, d.y, cutoff=0.0, kernel="triangular", bandwidth=h,
                      run_id="polyfit-check")
    for side_arr, side_res in (
        ((0.0 - d.x[d.x < 0], d.y[d.x < 0]), res.left),
        ((d.x[d.x > 0], d.y[d.x > 0]), res.right),
    ):
        dist, yv = side_arr
        u = np.abs(dist) / h
        w = np.where(u <= 1.0, 1.0 - u, 0.0)
        keep = w > 0
        assert side_res.intercept == pytest.approx(
            polyfit_boundary_intercept(dist[keep], yv[keep], w[keep]), abs=1e-7)

    # Uniform kernel special case must agree with scipy.stats.linregress OLS.
    res_u = rd_estimate(d.x, d.y, cutoff=0.0, kernel="uniform", bandwidth=h,
                        run_id="linregress-check")
    dl = 0.0 - d.x[d.x < 0]; yl = d.y[d.x < 0]
    dr = d.x[d.x > 0]; yr = d.y[d.x > 0]
    kl = np.abs(dl) <= h; kr = dr <= h
    assert res_u.left.intercept == pytest.approx(
        linregress_uniform_boundary(dl[kl], yl[kl]), abs=1e-7)
    assert res_u.right.intercept == pytest.approx(
        linregress_uniform_boundary(dr[kr], yr[kr]), abs=1e-7)


def test_it_is_not_a_raw_mean_difference():
    # Asymmetric support + steep slope: the raw side-mean difference is badly
    # biased for the jump, while the local-linear boundary estimate is not.
    rng = np.random.default_rng(13)
    tau, slope = 5.0, 3.0
    xl = rng.uniform(-2.0, 0.0, 2000)
    xr = rng.uniform(0.0, 1.0, 2000)
    x = np.concatenate([xl, xr])
    y = slope * x + np.where(x >= 0, tau, 0.0) + rng.normal(0, 0.3, x.size)
    raw_diff = y[x >= 0].mean() - y[x < 0].mean()
    res = rd_estimate(x, y, cutoff=0.0, bandwidth=0.25, run_id="not-raw-means")
    # Raw means are far from the truth ...
    assert abs(raw_diff - tau) > 2.0
    # ... the local-linear jump is accurate and agrees with the reference.
    assert res.tau == pytest.approx(tau, abs=0.25)
    ref = rd_reference(x, y, cutoff=0.0, h=0.25)
    assert res.tau == pytest.approx(ref.tau, abs=1e-7)


# --------------------------------------------------------------------------- #
# The four required validation scenarios
# --------------------------------------------------------------------------- #

def test_known_jump_is_recovered_and_ci_excludes_zero():
    d = sharp_jump(n=4000, tau=10.0, noise=1.0, seed=21)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth="ik", run_id="jump-ik")
    assert res.status is not RunStatus.FAILED
    assert res.tau == pytest.approx(10.0, abs=0.6)
    assert res.ci is not None
    assert res.ci[0] > 9.0 and res.ci[1] < 11.0
    assert res.pvalue < 1e-6
    assert res.left.n >= 10 and res.right.n >= 10   # effective sample reported


def test_no_jump_scenario_ci_covers_zero():
    d = no_jump(n=4000, slope=-2.0, curvature=3.0, noise=1.0, seed=22)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth="ik", run_id="nojump")
    assert res.tau == pytest.approx(0.0, abs=0.5)
    assert res.ci is not None
    assert res.ci[0] < 0.0 < res.ci[1]
    assert res.pvalue > 0.05


def test_density_discontinuity_does_not_bias_level_but_triggers_mccrary():
    d = density_discontinuity(n=4000, right_share=0.75, seed=23)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=0.25, run_id="dens")
    # Conditional mean is continuous: jump estimate stays near zero.
    assert res.tau == pytest.approx(0.0, abs=0.3)
    mcc = res.diagnostics["mccrary"]
    assert mcc["passed"] is False
    # Density on the right is 3x the left -> log ratio ~ log(3) = 1.099.
    assert mcc["details"]["theta_log_density_jump"] == pytest.approx(1.099, abs=0.35)
    assert mcc["details"]["pvalue"] < 0.01
    assert any("McCrary" in w for w in res.warnings)


def test_sparse_boundary_hard_gap_is_rejected_not_extrapolated():
    d = sparse_boundary(n=800, inner_gap=0.25, tau=4.0, seed=24)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=0.1, run_id="sparse")
    assert res.status is RunStatus.FAILED
    assert res.failure_category is FailureCategory.NON_IDENTIFIABLE
    assert res.tau is None and res.ci is None
    assert res.diagnostics["identifiability"]["left"]["extrapolation_factor"] >= 1.0
    # A larger fixed bandwidth cannot manufacture data in the gap either.
    res2 = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth=0.5, run_id="sparse2")
    assert res2.status is RunStatus.FAILED
    assert res2.failure_category is FailureCategory.NON_IDENTIFIABLE


def test_heaped_discrete_data_is_flagged_but_still_estimable():
    d = heaped_discrete(n=3000, step=0.1, tau=5.0, seed=25)
    res = rd_estimate(d.x, d.y, cutoff=d.cutoff, bandwidth="rot", run_id="heaped")
    # Estimate produced ...
    assert res.status is RunStatus.WARNING
    assert res.tau == pytest.approx(5.0, abs=0.4)
    # ... with the discreteness + heaping diagnostics explicit.
    assert res.diagnostics["discreteness"]["passed"] is False
    assert res.diagnostics["heaping"]["passed"] is False
    assert "bandwidth_adjustments" in res.diagnostics
    # Observations exactly at the cutoff never enter either fit.
    assert res.diagnostics["sample"]["n_at_cutoff_excluded"] > 0


# --------------------------------------------------------------------------- #
# Failure categories - each unsuccessful state is explicit
# --------------------------------------------------------------------------- #

def test_too_few_observations_is_insufficient_data():
    x = np.array([-0.5, -0.4, 0.4, 0.5])
    y = np.array([1.0, 1.1, 2.0, 2.1])
    res = rd_estimate(x, y, run_id="few")
    assert res.status is RunStatus.FAILED
    assert res.failure_category is FailureCategory.INSUFFICIENT_DATA


def test_one_empty_side_is_insufficient_data():
    x = np.linspace(0.05, 1.0, 50)
    y = x + 1.0
    res = rd_estimate(x, y, run_id="oneside")
    assert res.status is RunStatus.FAILED
    assert res.failure_category is FailureCategory.INSUFFICIENT_DATA
    assert "left" in (res.failure_reason or "")


def test_mismatched_lengths_and_nonfinite_are_bad_input():
    res = rd_estimate(np.array([-1.0, 1.0]), np.array([0.0, 1.0, 2.0]),
                      run_id="mismatch")
    assert res.failure_category is FailureCategory.BAD_INPUT
    res2 = rd_estimate(np.array([-1.0, np.nan, 1.0]),
                       np.array([0.0, 1.0, 2.0]), run_id="nan")
    assert res2.failure_category is FailureCategory.BAD_INPUT


def test_unknown_kernel_and_bad_bandwidth_are_bad_input():
    x = np.linspace(-1, 1, 40)
    y = x * 2
    assert rd_estimate(x, y, kernel="no-such-kernel", run_id="k").failure_category \
        is FailureCategory.BAD_INPUT
    assert rd_estimate(x, y, bandwidth=-1.0, run_id="bw").failure_category \
        is FailureCategory.BAD_INPUT
    assert rd_estimate(x, y, se_type="wild", run_id="se").failure_category \
        is FailureCategory.BAD_INPUT


def test_identical_x_on_a_side_is_rank_deficient():
    xl = np.full(30, -0.5)
    xr = np.linspace(0.05, 1.0, 30)
    x = np.concatenate([xl, xr])
    y = np.concatenate([np.zeros(30), np.ones(30)])
    res = rd_estimate(x, y, bandwidth=0.2, run_id="rank")
    assert res.status is RunStatus.FAILED
    assert res.failure_category in (
        FailureCategory.RANK_DEFICIENT, FailureCategory.NON_IDENTIFIABLE)


# --------------------------------------------------------------------------- #
# Reported evidence: effective sample, identifiable range, versions, CI/bias
# --------------------------------------------------------------------------- #

def test_result_reports_effective_sample_and_identifiable_range():
    d = sharp_jump(n=2000, seed=31)
    res = rd_estimate(d.x, d.y, bandwidth=0.3, run_id="evidence")
    es = res.diagnostics["effective_sample"]
    assert es["left_n"] > 0 and es["right_n"] > 0
    assert es["left_sum_weights"] > 0 and es["right_sum_weights"] > 0
    rng_ = res.diagnostics["identifiable_range"]
    assert rng_["left"][0] < 0.0 <= rng_["right"][1]
    assert res.versions["numpy"] == np.__version__
    assert "bias_bound_abs" in res.diagnostics["bias"]


def test_const_vs_robust_se_both_finite_and_ordered_reasonably():
    d = sharp_jump(n=3000, seed=32)
    r_const = rd_estimate(d.x, d.y, bandwidth=0.3, se_type="const", run_id="sec")
    r_hc2 = rd_estimate(d.x, d.y, bandwidth=0.3, se_type="hc2", run_id="seh")
    assert np.isfinite(r_const.se) and np.isfinite(r_hc2.se)
    assert r_const.tau == pytest.approx(r_hc2.tau, abs=1e-9)  # same point est.
