"""Per-fold standardization must use training rows only (criterion 2)."""

from __future__ import annotations

import numpy as np

from aipw_backend.folds import make_folds
from aipw_backend.scaling import design_matrix, fit_scaler


def test_fold_scaler_equals_training_partition_statistics():
    rng = np.random.default_rng(7)
    x = rng.standard_normal((500, 3)) * np.array([2.0, 5.0, 0.1])
    a = (rng.random(500) < 0.5).astype(int)
    split = make_folds(a, n_splits=5, seed=11)
    for k in range(5):
        tr = split.train_idx[k]
        scaler = fit_scaler(x[tr])
        np.testing.assert_allclose(scaler.mean, np.mean(x[tr], axis=0))
        np.testing.assert_allclose(
            scaler.scale, np.std(x[tr], axis=0, ddof=0)
        )


def test_training_only_scaler_differs_from_full_data_scaler():
    """With a shifted validation fold the two constructions disagree; this is
    the signal a leakage audit relies on."""
    rng = np.random.default_rng(8)
    x = rng.standard_normal((400, 1))
    a = np.r_[np.ones(200, dtype=int), np.zeros(200, dtype=int)]
    split = make_folds(a, n_splits=4, seed=3)
    k = 0
    tr, va = split.train_idx[k], split.valid_idx[k]
    # Engineer distribution shift on the held-out fold.
    x = x.copy()
    x[va] += 5.0
    train_scaler = fit_scaler(x[tr])
    leaky_scaler = fit_scaler(x)  # forbidden: sees validation rows
    assert abs(train_scaler.mean[0] - leaky_scaler.mean[0]) > 0.5
    z_correct = train_scaler.transform(x[va])
    z_leaky = leaky_scaler.transform(x[va])
    assert np.abs(z_correct.mean()) > 3.0  # shift preserved -> honest
    assert np.abs(z_leaky.mean()) < np.abs(z_correct.mean())  # shift erased


def test_constant_column_scales_to_one_not_nan():
    x = np.zeros((50, 2))
    x[:, 0] = 3.0
    x[:, 1] = np.linspace(0, 1, 50)
    scaler = fit_scaler(x)
    z = scaler.transform(x)
    assert np.all(np.isfinite(z))
    assert np.all(z[:, 0] == 0.0)
    assert scaler.scale[0] == 1.0


def test_design_matrix_has_intercept_and_keeps_shape():
    rng = np.random.default_rng(1)
    x = rng.standard_normal((20, 2))
    scaler = fit_scaler(x)
    d = design_matrix(x[:5], scaler)
    assert d.shape == (5, 3)
    np.testing.assert_allclose(d[:, 0], 1.0)
