"""Fold construction and out-of-fold alignment tests."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import Config, Dataset, StateConflictError
from aipw.crossfit import cross_fit, make_stratified_folds


def _tiny_dataset(n: int, seed: int = 0) -> Dataset:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 2))
    a = (rng.uniform(size=n) > 0.5).astype(float)
    y = rng.normal(size=n)
    return Dataset(x=x, a=a, y=y)


def test_folds_form_exact_partition_and_are_stratified():
    ds = _tiny_dataset(200)
    folds = make_stratified_folds(ds, 5, np.random.default_rng(3))
    assert folds.shape == (200,)
    assert set(np.unique(folds)) == set(range(5))
    counts = np.bincount(folds)
    assert counts.sum() == 200
    # strata are dealt round-robin independently, so the TOTAL sizes can
    # differ by up to 2 (one residual row per stratum)
    assert counts.max() - counts.min() <= 2
    # stratification: every fold contains both treatment levels
    for f in range(5):
        assert set(np.unique(ds.a[folds == f])) == {0.0, 1.0}


def test_fold_ids_are_reproducible_from_seed():
    ds = _tiny_dataset(200)
    f1 = make_stratified_folds(ds, 5, np.random.default_rng(7))
    f2 = make_stratified_folds(ds, 5, np.random.default_rng(7))
    np.testing.assert_array_equal(f1, f2)


def test_too_many_folds_is_input_error():
    from aipw.contract import InputError
    ds = _tiny_dataset(5)
    with pytest.raises(InputError):
        make_stratified_folds(ds, 5, np.random.default_rng(0))


def test_oof_predictions_never_use_the_predicted_rows_itself():
    # Spy by poisoning: build a dataset where adding a row to the training set
    # changes the fitted model detectably, then assert each row's OOF prediction
    # equals the refit on OTHER rows (done via diagnostics independently in
    # test_diagnostics.py); here we assert shape, finiteness and PS bounds.
    cfg = Config.default()
    d = _tiny_dataset(300, seed=5)
    folds = make_stratified_folds(d, cfg.folds, np.random.default_rng(0))
    oof = cross_fit(d, cfg, folds)
    assert oof.propensity.shape == (300,)
    assert np.all((oof.propensity > 0) & (oof.propensity < 1))
    assert np.all(np.isfinite(oof.mu0)) and np.all(np.isfinite(oof.mu1))
    assert len(oof.diagnostics) == cfg.folds
    for diag in oof.diagnostics:
        assert diag.valid_size + diag.train_size == 300


def test_permuted_fold_numbering_is_detected(config, scenario):
    # A fold-id vector that permutes fold labels AFTER prediction would align
    # each row with a prediction produced for DIFFERENT rows. The partition
    # checker only validates shape/range; the OOF refit checker must catch it.
    from aipw.diagnostics import verify_oof_predictions
    folds = make_stratified_folds(
        scenario.dataset, config.folds, np.random.default_rng(0))
    oof = cross_fit(scenario.dataset, config, folds)

    # Deterministic cyclic misalignment: every row of fold f receives the
    # prediction produced for a row of fold (f+1) mod k (round-robin within
    # the source fold, so unequal fold sizes are harmless). Every single OOF
    # value is then attached to the wrong row.
    k = config.folds
    bad_ps, bad_m0, bad_m1 = [np.empty_like(oof.propensity) for _ in range(3)]
    for f in range(k):
        dst = np.flatnonzero(folds == f)
        src = np.flatnonzero(folds == (f + 1) % k)
        pick = src[np.arange(dst.size) % src.size]
        bad_ps[dst] = oof.propensity[pick]
        bad_m0[dst] = oof.mu0[pick]
        bad_m1[dst] = oof.mu1[pick]
    from aipw.crossfit import OOFPredictions
    swapped = OOFPredictions(
        fold_id=oof.fold_id, propensity=bad_ps,
        mu0=bad_m0, mu1=bad_m1,
        diagnostics=oof.diagnostics,
        scalers0=oof.scalers0, scalers1=oof.scalers1)
    with pytest.raises(StateConflictError, match="out-of-fold"):
        verify_oof_predictions(scenario.dataset, config, folds, swapped)


def test_fold_id_off_by_one_is_detected(config, scenario):
    folds = make_stratified_folds(
        scenario.dataset, config.folds, np.random.default_rng(0))
    bad = folds.copy()
    bad[bad == config.folds - 1] = config.folds  # out of range
    with pytest.raises(StateConflictError, match="fold ids out of range"):
        cross_fit(scenario.dataset, config, bad)


def test_row_misaligned_oof_array_is_detected(config, scenario):
    folds = make_stratified_folds(
        scenario.dataset, config.folds, np.random.default_rng(0))
    oof = cross_fit(scenario.dataset, config, folds)
    from aipw.crossfit import OOFPredictions
    truncated = OOFPredictions(
        fold_id=oof.fold_id, propensity=oof.propensity[:-1],
        mu0=oof.mu0, mu1=oof.mu1, diagnostics=oof.diagnostics,
        scalers0=oof.scalers0, scalers1=oof.scalers1)
    with pytest.raises(StateConflictError, match="misaligned"):
        truncated.assert_alignment(scenario.dataset.n)
