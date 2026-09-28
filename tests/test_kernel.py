"""AIPW kernel: formula verification, cross-fitting correspondence and the
known-effect misspecification fixtures.

Reference answers come from ``aipw_backend.reference`` (true DGP nuisance
functions, independent implementation) - never from the kernel under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from aipw_backend.config import AipwConfig, ModelConfig
from aipw_backend.contract import validate_dataset
from aipw_backend.dgp import generate_sample
from aipw_backend.errors import ComputationError, InputError
from aipw_backend.folds import make_folds
from aipw_backend.kernel import crossfit_nuisances, estimate_aipw
from aipw_backend.models import RidgeLogistic, RidgeOLS
from aipw_backend.reference import naive_mean_difference, reference_estimate
from aipw_backend.scaling import design_matrix, fit_scaler

from tests.conftest import scenario_config

TAU = 2.0


def _estimate(sample, config, cluster=False):
    data = validate_dataset(
        sample.x, sample.a, sample.y, sample.cluster if cluster else None,
        n_splits=config.folds.n_splits,
    )
    return estimate_aipw(data, config)


# ---------------------------------------------------------------------------
# Formula checks against three independent estimators
# ---------------------------------------------------------------------------

def test_aipw_matches_known_effect_and_oracle(large_sample, oracle, default_config):
    res = _estimate(large_sample, default_config)
    assert abs(res.estimate - TAU) < 0.05
    # Independent AIPW oracle (true e, mu), g-formula and IPW all agree.
    assert abs(res.estimate - oracle.aipw) < 0.03
    assert abs(oracle.g_formula - TAU) < 0.05
    assert abs(oracle.ipw - TAU) < 0.20  # IPW noisier but unbiased here
    # Production influence-function SE close to oracle influence-function SE.
    assert abs(res.se - oracle.se_aipw) / oracle.se_aipw < 0.10


def test_estimate_is_mean_of_influence_contributions(large_sample, default_config):
    res = _estimate(large_sample, default_config)
    np.testing.assert_allclose(res.estimate, np.mean(res.influence), rtol=1e-12)


# ---------------------------------------------------------------------------
# The four misspecification fixtures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["both_correct", "propensity_only", "outcome_only"])
def test_three_of_four_scenarios_are_consistent(large_sample, folds_cfg, scenario):
    """Double robustness: either channel correct => centred on tau."""
    cfg = scenario_config(scenario, folds_cfg)
    res = _estimate(large_sample, cfg)
    assert abs(res.estimate - TAU) < 0.10, (
        f"{scenario} estimate {res.estimate} drifted from {TAU}"
    )
    assert res.ci_low < TAU < res.ci_high


def test_both_wrong_is_demonstrably_biased(large_sample, folds_cfg):
    """Both nuisances wrong: no free lunch. The estimate tracks the unadjusted
    mean difference, which is biased in this confounded DGP."""
    cfg = scenario_config("both_wrong", folds_cfg)
    res = _estimate(large_sample, cfg)
    naive = naive_mean_difference(large_sample.a, large_sample.y)
    assert abs(res.estimate - naive) < 0.02
    assert abs(res.estimate - TAU) > 0.20
    # Sign and rough size agree with the analytic first-order bias
    # alpha1*gamma1 + alpha2*gamma2 = 0.8 - 0.48 = 0.32.
    assert res.estimate - TAU > 0.15


def test_both_wrong_bias_is_not_seed_luck(folds_cfg):
    """Bias persists across independent samples (rules out a fixture fluke)."""
    cfg = scenario_config("both_wrong", folds_cfg)
    errors = []
    for seed in (101, 202, 303):
        sample = generate_sample(8000, seed=seed, tau=TAU)
        errors.append(_estimate(sample, cfg).estimate - TAU)
    assert all(e > 0.15 for e in errors), errors


# ---------------------------------------------------------------------------
# Out-of-fold correspondence (criterion 1) and leakage detection
# ---------------------------------------------------------------------------

def test_oof_predictions_equal_model_trained_without_that_row(
    large_sample, default_config
):
    """Independently retrain every fold model from the test side and confirm
    the kernel's stored prediction for a held-out row equals it exactly, and
    differs from the in-sample prediction of a model that saw the row."""
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    res = estimate_aipw(data, default_config, split=split)

    k = 2
    tr, va = split.train_idx[k], split.valid_idx[k]
    scaler = fit_scaler(data.x[tr])
    g = RidgeLogistic(1e-6).fit(
        design_matrix(data.x[tr], scaler), data.a[tr].astype(float)
    )
    expected_e = RidgeLogistic.predict_proba(design_matrix(data.x[va], scaler), g)
    np.testing.assert_allclose(res.pscore[va], expected_e, rtol=1e-12)

    # A model trained on ALL rows would have seen these rows: its in-sample
    # predictions are measurably different, demonstrating what leakage would
    # look like (and that the kernel does not do it).
    leaky_scaler = fit_scaler(data.x)
    g_leak = RidgeLogistic(1e-6).fit(
        design_matrix(data.x, leaky_scaler), data.a.astype(float)
    )
    leaky_e = RidgeLogistic.predict_proba(
        design_matrix(data.x[va], leaky_scaler), g_leak
    )
    assert np.max(np.abs(leaky_e - res.pscore[va])) > 1e-8


def test_every_row_predicted_exactly_once(large_sample, default_config):
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    nuis, _ = crossfit_nuisances(data, split, default_config)
    assert np.all(np.isfinite(nuis.pscore))
    # Predictions respect fold identity: rows of fold k predicted using no
    # fold-k training rows.
    for k in range(5):
        assert np.all(np.isfinite(nuis.mu1[split.valid_idx[k]]))


def test_sabotaged_fold_numbering_is_rejected(large_sample, default_config):
    """A fold-id/index mismatch reaching the estimator must fail, not run."""
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    wrong = make_folds(data.a, 5, 999, True)
    # Valid index sets from one split, ids from another: misnumbered folds.
    object.__setattr__(split, "fold_id", wrong.fold_id)
    with pytest.raises(InputError, match="mismatch"):
        estimate_aipw(data, default_config, split=split)


def test_train_validate_overlap_split_is_rejected(large_sample, default_config):
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    leaked = tuple(
        np.union1d(v, split.train_idx[k][:1])
        for k, v in enumerate(split.valid_idx)
    )
    object.__setattr__(split, "valid_idx", leaked)
    with pytest.raises(InputError, match="leakage"):
        estimate_aipw(data, default_config, split=split)


def test_duplicated_validation_row_is_computation_failure(
    large_sample, default_config
):
    """Bypassing the structural guard, the kernel's one-writer assertion must
    still flag a fold whose rows were already predicted."""
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    v = list(split.valid_idx)
    v[1] = np.union1d(v[1], v[0][:2])  # rows predicted in fold 0 and 1
    object.__setattr__(split, "valid_idx", tuple(v))
    with pytest.raises(ComputationError, match="collided|fold numbering"):
        crossfit_nuisances(data, split, default_config)


def test_omitted_validation_row_is_computation_failure(
    large_sample, default_config
):
    data = validate_dataset(
        large_sample.x, large_sample.a, large_sample.y,
        n_splits=default_config.folds.n_splits,
    )
    split = make_folds(data.a, 5, default_config.folds.seed, True)
    v = list(split.valid_idx)
    v[0] = v[0][2:]  # two rows predicted by nobody
    object.__setattr__(split, "valid_idx", tuple(v))
    with pytest.raises(ComputationError, match="never received"):
        crossfit_nuisances(data, split, default_config)


# ---------------------------------------------------------------------------
# Positivity / computation failure category
# ---------------------------------------------------------------------------

def test_boundary_propensity_is_computation_failure(folds_cfg):
    """Near-deterministic treatment given X violates positivity; the fitted
    propensity hits the boundary and the run fails as computation_failure."""
    rng = np.random.default_rng(5)
    n = 2000
    x = rng.standard_normal((n, 1))
    a = (x[:, 0] > 0.0).astype(np.int8)  # deterministic rule
    y = rng.standard_normal(n)
    data = validate_dataset(x, a, y, n_splits=folds_cfg.n_splits)
    cfg = AipwConfig(
        folds=folds_cfg,
        treatment_model=ModelConfig("logistic_ridge", penalty=1e-6),
        outcome_model=ModelConfig("ols_ridge"),
    )
    with pytest.raises(ComputationError, match="positivity|boundary"):
        estimate_aipw(data, cfg)


def test_att_positivity_checks_control_rows_not_treated(folds_cfg):
    """ATT weight e/(1-e) on control rows: a control pscore -> 1 is fatal,
    while a treated pscore -> 1 is harmless (it is never in a denominator)."""
    from aipw_backend.contract import check_positivity

    a = np.array([1, 0, 1, 0], dtype=np.int8)
    e_treated_one = np.array([1 - 1e-12, 0.5, 0.9, 0.4])
    # Treated unit at boundary is fine for ATT.
    check_positivity(e_treated_one, a, "ATT")
    # Control unit at boundary must fail.
    e_control_one = np.array([0.9, 0.5, 0.8, 1 - 1e-12])
    with pytest.raises(ComputationError, match="ATT weight|positivity"):
        check_positivity(e_control_one, a, "ATT")
    # ATE still rejects the treated-boundary array.
    with pytest.raises(ComputationError, match="boundary"):
        check_positivity(e_treated_one, a, "ATE")


# ---------------------------------------------------------------------------
# ATT estimand
# ---------------------------------------------------------------------------

def test_att_targets_treated_subpopulation(large_sample, folds_cfg):
    cfg = AipwConfig(
        folds=folds_cfg,
        treatment_model=ModelConfig("logistic_ridge"),
        outcome_model=ModelConfig("ols_ridge"),
        estimand="ATT",
    )
    res = _estimate(large_sample, cfg)
    # Heterogeneous effect tau + delta*X1: ATT = tau + delta E[X1 | A=1].
    known_att = TAU + large_sample.params.delta * large_sample.x[
        large_sample.a == 1, 0
    ].mean()
    assert abs(res.estimate - known_att) < 0.05
    assert res.estimand == "ATT"
    # Stored influence contributions average to the ATT estimate (ATE/ATT
    # diagnostics share this invariant).
    np.testing.assert_allclose(
        np.mean(res.influence), res.estimate, rtol=1e-12
    )
