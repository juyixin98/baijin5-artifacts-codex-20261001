"""Nuisance model tests: closed-form checks and train-only standardization."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import ComputationFailure, InputError
from aipw.models import LogisticRegression, OLSRidge, Standardizer


def test_standardizer_uses_training_rows_only():
    rng = np.random.default_rng(0)
    x_train = rng.normal(loc=5.0, scale=2.0, size=(500, 1))
    x_valid = rng.normal(loc=-3.0, scale=1.0, size=(100, 1))
    scaler = Standardizer().fit(x_train)

    # fitted stats equal TRAIN stats exactly, never pooled stats
    np.testing.assert_allclose(scaler.mean_, x_train.mean(axis=0))
    np.testing.assert_allclose(scaler.scale_, x_train.std(axis=0))
    pooled = np.vstack([x_train, x_valid])
    assert abs(scaler.mean_[0] - pooled.mean()) > 1.0

    # train rows centered; validation rows, under shift, are NOT centered
    assert abs(scaler.transform(x_train).mean()) < 1e-12
    assert abs(scaler.transform(x_valid).mean()) > 1.0

    # constant columns are scaled to 1 rather than dividing by zero
    const = Standardizer().fit(np.ones((10, 2)))
    np.testing.assert_allclose(const.scale_, [1.0, 1.0])
    np.testing.assert_allclose(const.transform(np.full((3, 2), 3.0)),
                               np.full((3, 2), 2.0))


def test_standardizer_transform_before_fit_is_computation_error():
    with pytest.raises(ComputationFailure):
        Standardizer().transform(np.zeros((2, 2)))


def test_logistic_recovers_known_coefficients():
    rng = np.random.default_rng(1)
    n = 20_000
    x = rng.normal(size=(n, 2))
    beta = np.array([0.3, -1.2, 0.7])
    p = 1.0 / (1.0 + np.exp(-(np.column_stack([np.ones(n), x]) @ beta)))
    a = (rng.uniform(size=n) < p).astype(float)

    fitted = LogisticRegression(tol=1e-11).fit(x, a)
    np.testing.assert_allclose(fitted.beta_, beta, atol=0.08)
    proba = fitted.predict_proba(x)
    assert np.all((proba > 0) & (proba < 1))


def test_logistic_matches_independent_optimizer_on_small_data():
    # 8 rows: every design point appears under BOTH treatments -> strict
    # overlap, finite interior MLE. Package IRLS vs an independent optimizer.
    from scipy.optimize import minimize
    x = np.array([[0.0, 0.0], [0.0, 0.0],
                  [1.0, 0.0], [1.0, 0.0],
                  [0.0, 1.0], [0.0, 1.0],
                  [1.0, 1.0], [1.0, 1.0]])
    a = np.array([0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0])
    z = np.column_stack([np.ones(8), x])

    def loss(b):
        eta = z @ b
        return float(np.sum(np.logaddexp(0, eta) - a * eta))

    opt = minimize(loss, np.zeros(3), method="Nelder-Mead", tol=1e-12,
                   options={"maxiter": 20000})
    got = LogisticRegression(tol=1e-11).fit(x, a).beta_
    np.testing.assert_allclose(got, opt.x, atol=1e-5)


def test_logistic_single_treatment_level_is_computation_failure():
    x = np.random.default_rng(0).normal(size=(20, 2))
    with pytest.raises(ComputationFailure) as exc:
        LogisticRegression().fit(x, np.ones(20))
    assert "single treatment level" in str(exc.value)


def test_logistic_separation_does_not_return_finite_mle_quietly():
    # complete separation: the MLE is at infinity; the solver must fail loudly
    # (non-convergence or non-finite coefficients), never return a finite fit.
    x = np.column_stack([np.where(np.arange(40) % 2 == 0, -3.0, 3.0),
                         np.zeros(40)])
    a = (np.arange(40) % 2).astype(float)
    with pytest.raises(ComputationFailure) as exc:
        LogisticRegression(max_iter=100).fit(x, a)
    assert ("did not converge" in exc.value.message
            or "non-finite" in exc.value.message
            or "singular" in exc.value.message)


def test_ols_matches_normal_equation_on_raw_columns():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(300, 3))
    beta = np.array([2.0, -1.0, 0.5, 1.5])
    y = np.column_stack([np.ones(300), x]) @ beta + rng.normal(scale=0.01, size=300)
    model = OLSRidge().fit(x, y)
    # standardized+intercept design spans the same space as raw+intercept
    np.testing.assert_allclose(model.predict(x),
                               np.column_stack([np.ones(300), x]) @ beta, atol=0.02)
    # train-only scaler: prediction on shifted rows differs from an all-data fit
    x_more = np.vstack([x, rng.normal(loc=10.0, size=(50, 3))])
    leaky = OLSRidge().fit(x_more, np.concatenate([y, rng.normal(size=50) + 20]))
    clean = OLSRidge().fit(x, y)
    assert not np.allclose(clean.predict(x_more[:5]), leaky.predict(x_more[:5]))


def test_ols_rejects_more_coefficients_than_rows():
    # 4 treated rows, 3 covariates + intercept -> rank-deficient per-arm fit
    x = np.arange(12, dtype=float).reshape(4, 3)
    y = np.array([1.0, 2.0, 3.0, 4.0])
    with pytest.raises(ComputationFailure, match="fewer observations"):
        OLSRidge().fit(x, y)
