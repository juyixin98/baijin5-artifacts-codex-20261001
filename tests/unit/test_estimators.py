"""Kernel tests against hand-computed values and an independent QR reference.

The hand dataset (``conftest.hand_dataset``) is built with a *constructed*
baseline imbalance (control X=1..4, treatment X=5..8) so that:

* the unadjusted difference is 14.0 although the true effect is 2.0,
* control-arm CUPED theta is exactly 2.97 -> adjusted effect 2.12,
* pooled within-arm slope is exactly 3.02 -> ANCOVA / Lin effect 1.92,
* a *given* theta of 3.0 recovers the exact true effect 2.0.

These numbers are derived by hand in the test module, then also checked
against the independent QR-based reference implementation.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.core.contracts import (
    ErrorCode,
    EstimationError,
    SEType,
    Settings,
    ThetaSource,
)
from app.core.estimators import cuped, estimate_theta, group_difference, lin_ancova
from tests.conftest import (
    hand_dataset,
    prepare,
    ref_cuped,
    ref_lin_tau,
    ref_simple_slope,
    ref_welch,
)


# --------------------------------------------------------------------------- #
# Hand-computed constants
# --------------------------------------------------------------------------- #
TRUE_TAU = 2.0
HAND_UNADJ_DIFF = 14.0
HAND_UNADJ_SE = float(np.sqrt(7.6125))          # 2.7590759...
HAND_CONTROL_MEAN = 8.5
HAND_TREATMENT_MEAN = 22.5
HAND_CONTROL_THETA = 2.97
# theta is fitted (2.97, not the structural 3.0): control RSS = 0.0205,
# sigma^2 = RSS/(4-2) = 0.01025, Var(theta) = sigma^2/Sxx = 0.00205.
HAND_CONTROL_THETA_SE = float(np.sqrt(0.00205))  # 0.0452769...
HAND_POOLED_THETA = 3.02
HAND_CUPED_DIFF = 2.12
HAND_CUPED_SE = float(np.sqrt(0.01425))          # 0.1193734...
HAND_ANCOVA_DIFF = 1.92


# --------------------------------------------------------------------------- #
# Unadjusted estimator
# --------------------------------------------------------------------------- #
def test_unadjusted_hand_values(prepared_hand, case_log):
    est = group_difference(prepared_hand, SEType.WELCH)
    case_log("unadjusted comparison", estimate=est.estimate, se=est.se,
             n_control=est.n_control, n_treatment=est.n_treatment)
    assert est.estimate == pytest.approx(HAND_UNADJ_DIFF, abs=1e-12)
    assert est.se == pytest.approx(HAND_UNADJ_SE, rel=1e-9)
    assert est.df < est.n_control + est.n_treatment - 2  # Welch df < pooled df
    assert est.ci_low < HAND_UNADJ_DIFF < est.ci_high
    assert est.p_value < 0.01


def test_unadjusted_matches_independent_welch_reference(prepared_hand):
    d = prepared_hand
    ref = ref_welch(d.y[d.t == 1], d.y[d.t == 0])
    est = group_difference(d, SEType.WELCH)
    assert est.estimate == pytest.approx(ref["diff"], abs=1e-12)
    assert est.se == pytest.approx(ref["se"], rel=1e-12)
    assert est.df == pytest.approx(ref["df"], rel=1e-12)


def test_group_means_are_exact(prepared_hand):
    d = prepared_hand
    assert float(d.y[d.t == 0].mean()) == pytest.approx(HAND_CONTROL_MEAN)
    assert float(d.y[d.t == 1].mean()) == pytest.approx(HAND_TREATMENT_MEAN)


def test_welch_and_pooled_se_agree_on_balanced_arms(prepared_hand):
    # Equal-n identity: pooled SE algebraically equals Welch SE, but pooled
    # uses n-2 dof while Welch-Satterthwaite df is smaller.
    welch = group_difference(prepared_hand, SEType.WELCH)
    pooled = group_difference(prepared_hand, SEType.POOLED)
    assert welch.estimate == pooled.estimate
    assert welch.se == pytest.approx(pooled.se, rel=1e-12)
    assert pooled.df == 6.0
    assert welch.df < 6.0


def test_welch_and_pooled_se_diverge_on_unbalanced_arms():
    # Drop one control row -> n0=3, n1=4 and unequal within-arm variances.
    payload = hand_dataset()
    for col in ("y", "t", "x"):
        payload["data"][col] = payload["data"][col][1:]
    d = prepare(payload)
    welch = group_difference(d, SEType.WELCH)
    pooled = group_difference(d, SEType.POOLED)
    assert welch.se != pytest.approx(pooled.se)
    assert pooled.df == 5.0


def test_invalid_se_type_for_groups_is_config_error(prepared_hand):
    with pytest.raises(EstimationError) as exc:
        group_difference(prepared_hand, SEType.HC1)
    assert exc.value.code is ErrorCode.CONFIG_ERROR


# --------------------------------------------------------------------------- #
# CUPED
# --------------------------------------------------------------------------- #
def test_control_theta_is_exact_and_pre_period_sourced(prepared_hand, case_log):
    theta_fit = estimate_theta(prepared_hand, ThetaSource.CONTROL_PRE)
    case_log("theta estimated from control pre-period",
             theta=theta_fit.theta.tolist(), se=theta_fit.se.tolist(),
             r_squared=theta_fit.r_squared, n_used=theta_fit.n_used)
    assert theta_fit.source is ThetaSource.CONTROL_PRE
    assert theta_fit.n_used == 4
    assert theta_fit.theta[0] == pytest.approx(HAND_CONTROL_THETA, rel=1e-12)
    assert theta_fit.se[0] == pytest.approx(HAND_CONTROL_THETA_SE, rel=1e-9)
    assert theta_fit.r_squared == pytest.approx(1 - 0.0205 / 44.125, rel=1e-9)
    # Independent raw sums-of-squares reference.
    d = prepared_hand
    intercept, slope = ref_simple_slope(d.x[d.t == 0, 0], d.y[d.t == 0])
    assert slope == pytest.approx(HAND_CONTROL_THETA, abs=1e-12)
    assert intercept == pytest.approx(1.075, abs=1e-12)


def test_pooled_theta_is_average_of_within_arm_slopes(prepared_hand):
    theta_fit = estimate_theta(prepared_hand, ThetaSource.POOLED_PRE)
    assert theta_fit.theta[0] == pytest.approx(HAND_POOLED_THETA, abs=1e-12)
    assert theta_fit.n_used == 8


def test_cuped_hand_values_and_variance_reduction(prepared_hand, case_log):
    theta_fit = estimate_theta(prepared_hand, ThetaSource.CONTROL_PRE)
    est = cuped(prepared_hand, theta_fit, SEType.WELCH)
    case_log("CUPED adjusted", estimate=est.estimate, se=est.se,
             variance_reduction_vs_unadj=1 - (est.se / HAND_UNADJ_SE) ** 2)
    assert est.estimate == pytest.approx(HAND_CUPED_DIFF, rel=1e-12)
    assert est.se == pytest.approx(HAND_CUPED_SE, rel=1e-9)
    assert est.theta[0] == pytest.approx(HAND_CONTROL_THETA, rel=1e-12)
    assert est.theta_source == "control_pre"
    # Over 99% of SE removed relative to the unadjusted comparison.
    assert est.se / HAND_UNADJ_SE < 0.05


def test_cuped_matches_independent_reference(prepared_hand):
    theta_fit = estimate_theta(prepared_hand, ThetaSource.CONTROL_PRE)
    est = cuped(prepared_hand, theta_fit, SEType.WELCH)
    ref = ref_cuped(prepared_hand.y, prepared_hand.t, prepared_hand.x,
                    theta_fit.theta)
    assert est.estimate == pytest.approx(ref["diff"], abs=1e-12)
    assert est.se == pytest.approx(ref["se"], rel=1e-12)
    assert est.df == pytest.approx(ref["df"], rel=1e-12)


def test_given_theta_recovers_exact_true_effect(prepared_hand):
    # theta=3 (the structural slope) turns the biased 14.0 into exactly 2.0.
    theta_fit = estimate_theta(prepared_hand, ThetaSource.GIVEN,
                               given=np.array([3.0]))
    est = cuped(prepared_hand, theta_fit, SEType.WELCH)
    assert est.estimate == pytest.approx(TRUE_TAU, abs=1e-12)


def test_given_theta_wrong_length_is_config_error(prepared_hand):
    with pytest.raises(EstimationError) as exc:
        estimate_theta(prepared_hand, ThetaSource.GIVEN, given=np.array([1.0, 2.0]))
    assert exc.value.code is ErrorCode.CONFIG_ERROR


def test_cuped_rejects_regression_se_family(prepared_hand):
    theta_fit = estimate_theta(prepared_hand, ThetaSource.CONTROL_PRE)
    with pytest.raises(EstimationError) as exc:
        cuped(prepared_hand, theta_fit, SEType.HC1)
    assert exc.value.code is ErrorCode.CONFIG_ERROR


# --------------------------------------------------------------------------- #
# ANCOVA / Lin regression
# --------------------------------------------------------------------------- #
def test_ancova_main_effects_hand_value(prepared_hand):
    est = lin_ancova(prepared_hand, interactions=False, se_type=SEType.CLASSICAL)
    assert est.estimate == pytest.approx(HAND_ANCOVA_DIFF, abs=1e-12)


def test_lin_interactions_hand_value(prepared_hand, case_log):
    est = lin_ancova(prepared_hand, interactions=True, se_type=SEType.HC1)
    case_log("Lin interaction ATE", tau=est.estimate, se=est.se,
             extra=est.extra["interactions"])
    assert est.estimate == pytest.approx(HAND_ANCOVA_DIFF, abs=1e-12)
    assert est.extra["interactions"] is True


def test_lin_hc1_se_matches_independent_qr_reference(prepared_hand):
    for interactions in (False, True):
        est = lin_ancova(prepared_hand, interactions=interactions,
                         se_type=SEType.HC1)
        ref = ref_lin_tau(prepared_hand.y, prepared_hand.t, prepared_hand.x,
                          interactions)
        assert est.estimate == pytest.approx(ref["tau"], abs=1e-12)
        assert est.se == pytest.approx(ref["se_hc1"], rel=1e-12)


def test_lin_classical_se_matches_independent_qr_reference(prepared_hand):
    est = lin_ancova(prepared_hand, interactions=True, se_type=SEType.CLASSICAL)
    ref = ref_lin_tau(prepared_hand.y, prepared_hand.t, prepared_hand.x, True)
    assert est.se == pytest.approx(ref["se_classical"], rel=1e-12)


def test_hc1_se_larger_than_hc0_on_average_is_available(prepared_hand):
    hc1 = lin_ancova(prepared_hand, interactions=True, se_type=SEType.HC1)
    hc0 = lin_ancova(prepared_hand, interactions=True, se_type=SEType.HC0)
    # n/(n-p) correction factor 8/4 = 2 on the variance.
    assert hc1.se == pytest.approx(hc0.se * np.sqrt(8.0 / 4.0), rel=1e-12)


def test_regression_rejects_welch_family(prepared_hand):
    with pytest.raises(EstimationError) as exc:
        lin_ancova(prepared_hand, interactions=True, se_type=SEType.WELCH)
    assert exc.value.code is ErrorCode.CONFIG_ERROR


def test_collinear_design_is_typed(prepared_hand):
    payload = hand_dataset()
    payload["data"]["x_twin"] = payload["data"]["x"]
    payload["covariates"] = ["x", "x_twin"]
    d = prepare(payload)
    with pytest.raises(EstimationError) as exc:
        lin_ancova(d, interactions=False, se_type=SEType.CLASSICAL)
    assert exc.value.code is ErrorCode.COLLINEAR_COVARIATES


# --------------------------------------------------------------------------- #
# Randomised cross-checks against the independent reference
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("seed", [1, 7, 31])
def test_kernel_matches_qr_reference_on_random_data(seed):
    rng = np.random.default_rng(seed)
    n = 120
    x = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    t = (rng.uniform(size=n) < 0.5).astype(int)
    y = 0.5 + 1.3 * t + 2.0 * x - 0.7 * x2 + rng.normal(0, 1, n)
    payload = {
        "outcome_column": "y", "treatment_column": "t",
        "covariates": ["x", "x2"],
        "data": {"y": y.tolist(), "t": t.tolist(),
                 "x": x.tolist(), "x2": x2.tolist()},
    }
    d = prepare(payload)

    theta_fit = estimate_theta(d, ThetaSource.CONTROL_PRE)
    got = cuped(d, theta_fit, SEType.WELCH)
    ref = ref_cuped(y, t, np.column_stack([x, x2]), theta_fit.theta)
    assert got.estimate == pytest.approx(ref["diff"], abs=1e-11)
    assert got.se == pytest.approx(ref["se"], rel=1e-11)

    for interactions in (False, True):
        got_lin = lin_ancova(d, interactions=interactions, se_type=SEType.HC1)
        ref_lin = ref_lin_tau(y, t, np.column_stack([x, x2]), interactions)
        assert got_lin.estimate == pytest.approx(ref_lin["tau"], abs=1e-11)
        assert got_lin.se == pytest.approx(ref_lin["se_hc1"], rel=1e-11)
