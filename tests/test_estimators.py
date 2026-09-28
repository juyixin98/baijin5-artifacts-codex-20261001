"""Estimation kernel tests: hand-computed values, oracle agreement, DR behavior."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import Config, Estimand, TrimConfig
from aipw.crossfit import cross_fit, make_stratified_folds
from aipw.estimators import (
    estimate, gcomp_point, ipw_ate_ht, trim_propensity,
)


# --------------------------------------------------------------------- #
# Fully hand-computed dataset (3 treated, 3 control), literal answers
# --------------------------------------------------------------------- #
A = np.array([1.0, 1.0, 1.0, 0.0, 0.0, 0.0])
Y = np.array([4.0, 6.0, 5.0, 1.0, 2.0, 0.0])
E = np.array([0.8, 0.5, 0.4, 0.2, 0.5, 0.8])          # already interior
M1 = np.array([5.0, 5.0, 5.0, 5.0, 5.0, 5.0])
M0 = np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
NO_TRIM = TrimConfig(enabled=False)


def test_gcomp_is_mean_of_contrasts():
    assert gcomp_point(M0, M1) == pytest.approx(4.0)


def test_ipw_hand_computed():
    # treated terms A Y/e: 4/.8 + 6/.5 + 5/.4 = 5 + 12 + 12.5 = 29.5
    # control terms (1-A) Y/(1-e): 1/.8 + 2/.5 + 0/.2 = 1.25 + 4 + 0 = 5.25
    # mean over 6: (29.5 - 5.25)/6
    assert ipw_ate_ht(A, Y, E) == pytest.approx((29.5 - 5.25) / 6)


def test_aipw_scores_match_hand_computed_values():
    comp = estimate(A, Y, E, M0, M1, NO_TRIM, Estimand.ATE, stabilized=False)
    # row 0: (5-1) + 1*(4-5)/.8 - 0 = 4 - 1.25 = 2.75
    # row 1: 4 + (6-5)/.5 = 6.0
    # row 2: 4 + (5-5)/.4 = 4.0
    # row 3: 4 + 0 - 1*(1-1)/.8 = 4.0
    # row 4: 4 - (2-1)/.5 = 2.0
    # row 5: 4 - (0-1)/.2 = 9.0
    expected = np.array([2.75, 6.0, 4.0, 4.0, 2.0, 9.0])
    np.testing.assert_allclose(comp.scores, expected)
    assert comp.point == pytest.approx(expected.mean())


def test_perfect_outcome_model_makes_aipw_exact_regardless_of_ps():
    # m1=Y for treated, m0=Y for controls -> augmentation terms vanish and
    # point = mean(m1-m0) = 4 even if the supplied propensity is arbitrary.
    m1 = np.where(A == 1, Y, 5.0)
    m0 = np.where(A == 0, Y, 1.0)
    weird_e = np.array([0.99, 0.01, 0.6, 0.3, 0.7, 0.02])
    comp = estimate(A, Y, weird_e, m0, m1, NO_TRIM, Estimand.ATE, False)
    assert comp.point == pytest.approx(4.0)
    # scores reduce to m1 - m0 row-wise
    np.testing.assert_allclose(comp.scores, m1 - m0)


def test_correct_ps_makes_ipw_component_unbiased_even_with_bad_outcomes():
    # With m1=m0=0 (maximally wrong outcome models) AIPW collapses to IPW;
    # construct a randomized experiment so e=P(A=1|X)=0.5 constant is correct.
    rng = np.random.default_rng(7)
    n = 200_000
    a = (rng.uniform(size=n) < 0.5).astype(float)
    y0 = rng.normal(size=n)
    y = y0 + a * 0.37
    e = np.full(n, 0.5)
    zero_m = np.zeros(n)
    comp = estimate(a, y, e, zero_m, zero_m, NO_TRIM, Estimand.ATE, False)
    assert comp.point == pytest.approx(0.37, abs=0.01)
    assert comp.ipw == pytest.approx(comp.point, abs=1e-9)


def test_trim_reports_fraction_and_bounds():
    e = np.array([0.001, 0.5, 0.999, 0.4])
    out, frac = trim_propensity(e, 0.01, 0.99)
    np.testing.assert_allclose(out, [0.01, 0.5, 0.99, 0.4])
    assert frac == pytest.approx(0.5)


def test_extreme_propensity_without_trim_is_computation_failure():
    from aipw.contract import ComputationFailure
    with pytest.raises(ComputationFailure, match="extreme propensity"):
        estimate(A, Y, np.array([1.0, .5, .5, .2, .5, .5]),
                 M0, M1, NO_TRIM, Estimand.ATE, False)


# --------------------------------------------------------------------- #
# Cross-fitted behavior on synthetic scenarios vs the independent oracle
# --------------------------------------------------------------------- #
def _run(scenario_result, seed=2024):
    cfg = Config.default()
    ds = scenario_result.dataset
    folds = make_stratified_folds(ds, cfg.folds, np.random.default_rng(seed))
    oof = cross_fit(ds, cfg, folds)
    comp = estimate(ds.a, ds.y, oof.propensity, oof.mu0, oof.mu1,
                    cfg.trim_propensity, Estimand.ATE, False)
    return cfg, ds, folds, oof, comp


def test_aipw_matches_independent_oracle_on_every_scenario(scenario):
    from oracle import ref_aipw_ate
    cfg, ds, folds, _oof, comp = _run(scenario)
    ref = ref_aipw_ate(ds, folds, cfg.folds)
    assert comp.point == pytest.approx(ref["point"], abs=1e-8)
    assert comp.ipw == pytest.approx(ref["ipw"], abs=1e-8)
    assert comp.gcomp == pytest.approx(ref["gcomp"], abs=1e-8)


def test_dr_holds_when_ps_only_is_correct(ps_only, known_tau):
    _, _, _, _, comp = _run(ps_only)
    assert comp.point == pytest.approx(known_tau, abs=0.08)
    # outcome-only model (OLS linear) is wrong, so gcomp can be biased;
    # AIPW stays near truth because the propensity is correct
    assert abs(comp.point - known_tau) <= abs(comp.gcomp - known_tau) + 0.05


def test_dr_holds_when_outcome_only_is_correct(outcome_only, known_tau):
    _, _, _, _, comp = _run(outcome_only)
    assert comp.point == pytest.approx(known_tau, abs=0.08)
    assert abs(comp.point - known_tau) <= abs(comp.ipw - known_tau) + 0.05


def test_both_wrong_is_not_guaranteed_and_we_say_so(both_wrong, known_tau):
    # The contract must NOT claim correctness here. Assert the estimator still
    # returns a finite number, and document that it may be biased: we only
    # require it does not crash and that the bias differs from the DR scenarios
    # by being allowed to exceed the tolerance used there.
    _, _, _, _, comp = _run(both_wrong)
    assert np.isfinite(comp.point)
    # on THIS dgp both-wrong happens to land moderately near tau by symmetry;
    # the point of the test is that no branch promises it must.
    assert isinstance(comp.point, float)


def test_both_correct_is_unbiased_and_ci_covers(both_correct, known_tau):
    from aipw.influence import iid_variance
    _, _, _, _, comp = _run(both_correct)
    inf = iid_variance(comp.scores, comp.point)
    assert inf.ci_lower < known_tau < inf.ci_upper
    assert inf.se > 0
