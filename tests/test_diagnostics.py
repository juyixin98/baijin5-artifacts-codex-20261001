"""Diagnostics tests: leakage detection, OOF refit, estimator agreement."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import Config, StateConflictError
from aipw.crossfit import OOFPredictions, cross_fit, make_stratified_folds
from aipw.diagnostics import (
    audit_scaler_stats, estimator_agreement, verify_oof_predictions,
)
from fixture_utils import fake_scaler_stats, shifted_folds


def test_scaler_audit_passes_on_clean_pipeline(scenario, config):
    folds = make_stratified_folds(
        scenario.dataset, config.folds, np.random.default_rng(0))
    oof = cross_fit(scenario.dataset, config, folds)
    report = audit_scaler_stats(scenario.dataset, folds, config.folds, oof)
    assert report.passed is True
    for c in report.folds:
        assert c.mean_max_abs_diff < 1e-12
        assert c.scale_max_abs_diff < 1e-12
        # the leaky alternative MUST be distinguishable (audit has power)
        assert c.leaky_allrows_diff > 1e-6


def test_scaler_audit_detects_all_rows_leak(scenario, config):
    """Inject scalers fit on ALL arm rows: the audit must name them leaky."""
    ds = scenario.dataset
    folds = make_stratified_folds(ds, config.folds, np.random.default_rng(0))
    oof = cross_fit(ds, config, folds)
    leaky0 = fake_scaler_stats(folds, ds.x, ds.a, 0, config.folds, leaky=True)
    leaky1 = fake_scaler_stats(folds, ds.x, ds.a, 1, config.folds, leaky=True)
    tampered = OOFPredictions(
        fold_id=oof.fold_id, propensity=oof.propensity, mu0=oof.mu0,
        mu1=oof.mu1, diagnostics=oof.diagnostics,
        scalers0=leaky0, scalers1=leaky1)
    with pytest.raises(StateConflictError, match="leakage"):
        audit_scaler_stats(ds, folds, config.folds, tampered)


def test_scaler_audit_detects_partial_leak_one_fold_one_arm(scenario, config):
    """Leak in a single fold/arm must be pinpointed, not masked by others."""
    ds = scenario.dataset
    folds = make_stratified_folds(ds, config.folds, np.random.default_rng(0))
    oof = cross_fit(ds, config, folds)
    bad0 = list(oof.scalers0)
    f = config.folds - 1
    rows = ds.a == 0
    xx = ds.x[rows]
    bad0[f] = bad0[f].__class__(
        fold=f, mean=xx.mean(axis=0),
        scale=np.where(xx.std(axis=0) > 1e-12, xx.std(axis=0), 1.0))
    tampered = OOFPredictions(
        fold_id=oof.fold_id, propensity=oof.propensity, mu0=oof.mu0,
        mu1=oof.mu1, diagnostics=oof.diagnostics,
        scalers0=tuple(bad0), scalers1=oof.scalers1)
    with pytest.raises(StateConflictError) as exc:
        audit_scaler_stats(ds, folds, config.folds, tampered)
    assert exc.value.details["max_abs_diff"] > 1e-6


def test_shifted_folds_are_actually_shifted(both_correct, config):
    # guard for the leakage-power fixture: raw train/valid means differ a lot
    ds = both_correct.dataset
    folds = shifted_folds(ds, config.folds)
    f = 0
    tr, va = folds != f, folds == f
    shift = np.abs(ds.x[va].mean(0) - ds.x[tr].mean(0)) / ds.x[tr].std(0)
    assert shift.max() > 0.8


def test_independent_refit_matches_clean_oof(scenario, config):
    folds = make_stratified_folds(
        scenario.dataset, config.folds, np.random.default_rng(0))
    oof = cross_fit(scenario.dataset, config, folds)
    report = verify_oof_predictions(scenario.dataset, config, folds, oof)
    assert report.passed is True
    assert all(c.passed for c in report.folds)


def test_agreement_note_refuses_to_overclaim():
    agree = estimator_agreement(0.50, 0.51, 0.505)
    assert agree.abs_spread < 0.05
    assert "NOT proof" in agree.note
    disagree = estimator_agreement(0.2, 0.9, 0.5)
    assert disagree.abs_spread > 0.05
    assert "not salvaged" in disagree.note
