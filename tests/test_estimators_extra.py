"""Stabilized (augmented Hajek) and ATT kernel tests, incl. oracle agreement."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import Config, Estimand, TrimConfig
from aipw.crossfit import cross_fit, make_stratified_folds
from aipw.estimators import estimate
from aipw.influence import iid_variance


def _run(scenario_result, stabilized, seed=2024, estimand=Estimand.ATE):
    cfg = Config.from_dict({"stabilize_weight": stabilized})
    ds = scenario_result.dataset
    folds = make_stratified_folds(ds, cfg.folds, np.random.default_rng(seed))
    oof = cross_fit(ds, cfg, folds)
    comp = estimate(ds.a, ds.y, oof.propensity, oof.mu0, oof.mu1,
                    cfg.trim_propensity, estimand, stabilized)
    return cfg, ds, folds, comp


def test_score_mean_equals_point_for_stabilized_path(ps_only):
    _, _, _, comp = _run(ps_only, stabilized=True)
    assert comp.scores.mean() == pytest.approx(comp.point)


def test_stabilized_recovers_truth_when_ps_only_correct(ps_only, known_tau):
    _, _, _, comp = _run(ps_only, stabilized=True)
    assert comp.point == pytest.approx(known_tau, abs=0.08)
    assert np.isfinite(comp.ipw)


def test_stabilized_and_ht_agree_under_both_correct(both_correct, known_tau):
    _, _, _, c_ht = _run(both_correct, stabilized=False)
    _, _, _, c_hj = _run(both_correct, stabilized=True)
    # both are DR estimators; with both models well specified they must be
    # close even though their finite-sample forms differ
    assert c_ht.point == pytest.approx(c_hj.point, abs=0.03)
    assert c_hj.point == pytest.approx(known_tau, abs=0.05)


def test_stabilized_inference_is_finite(both_correct):
    _, _, _, comp = _run(both_correct, stabilized=True)
    inf = iid_variance(comp.scores, comp.point)
    assert inf.se > 0 and np.isfinite(inf.ci_lower)


def test_stabilized_perfect_outcome_model_is_exact():
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.normal(size=(n, 2))
    a = (rng.uniform(size=n) < 0.5).astype(float)
    y0 = 1.0 + x[:, 0]
    y = y0 + a * 0.5
    # perfect outcome models: both estimators return the exact ATE
    mu0 = 1.0 + x[:, 0]
    mu1 = mu0 + 0.5
    e = np.full(n, 0.5)
    comp = estimate(a, y, e, mu0, mu1, TrimConfig(enabled=False),
                    Estimand.ATE, stabilized=True)
    assert comp.point == pytest.approx(0.5, abs=1e-9)


def test_att_score_mean_invariant_and_finite(both_correct):
    _, ds, _, comp = _run(both_correct, stabilized=False, estimand=Estimand.ATT)
    assert comp.scores.mean() == pytest.approx(comp.point)
    inf = iid_variance(comp.scores, comp.point)
    assert inf.independent_units == ds.n


def test_att_recovers_known_tau_under_randomized_assignment():
    rng = np.random.default_rng(3)
    n = 20_000
    x = rng.normal(size=(n, 2))
    a = (rng.uniform(size=n) < 0.5).astype(float)
    y = 1.0 + x[:, 0] - 0.5 * x[:, 1] + a * 0.5 + rng.normal(scale=0.1, size=n)
    # randomized experiment: ATT = ATE = 0.5 even without cross-fitting
    mu0 = 1.0 + x[:, 0] - 0.5 * x[:, 1]
    mu1 = mu0
    e = np.full(n, 0.5)
    comp = estimate(a, y, e, mu0, mu1, TrimConfig(enabled=False),
                    Estimand.ATT, stabilized=False)
    assert comp.point == pytest.approx(0.5, abs=0.02)
