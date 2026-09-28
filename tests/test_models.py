"""Nuisance-model unit checks against hand-derived numbers."""

from __future__ import annotations

import numpy as np
import pytest

from aipw_backend.errors import ComputationError
from aipw_backend.models import RidgeLogistic, RidgeOLS, WrongConstant
from aipw_backend.scaling import design_matrix, fit_scaler


def test_ridge_ols_recovers_two_point_line():
    # Perfectly separable construction in feature space: y = 2 + 3 z.
    z = np.linspace(-2, 2, 41)
    x = np.c_[np.ones_like(z), z]
    y = 2.0 + 3.0 * z
    fit = RidgeOLS(penalty=0.0).fit(x, y)
    np.testing.assert_allclose(fit.coef, [2.0, 3.0], atol=1e-10)
    np.testing.assert_allclose(RidgeOLS.predict(x, fit), y)


def test_logistic_recovers_known_log_odds():
    rng = np.random.default_rng(0)
    z = rng.standard_normal(4000)
    # logit P(A=1) = 0.5 + 1.0 z
    p = 1.0 / (1.0 + np.exp(-(0.5 + z)))
    a = (rng.random(4000) < p).astype(float)
    x = np.c_[np.ones_like(z), z]
    fit = RidgeLogistic(penalty=0.0, max_iter=50).fit(x, a)
    np.testing.assert_allclose(fit.coef, [0.5, 1.0], atol=0.15)
    assert fit.converged and fit.n_iter < 50


def test_logistic_non_convergence_is_computation_error():
    # Fully separated data with zero penalty cannot converge in finite beta.
    x = np.array([[1.0, -10.0], [1.0, 10.0]])
    a = np.array([0.0, 1.0])
    with pytest.raises(ComputationError, match="did not converge"):
        RidgeLogistic(penalty=0.0, max_iter=20, tol=1e-12).fit(x, a)


def test_wrong_constant_predicts_sample_mean():
    rng = np.random.default_rng(2)
    x = rng.standard_normal((30, 2))
    target = rng.standard_normal(30) + 4.0
    m = WrongConstant()
    fit = m.fit(x, target)
    np.testing.assert_allclose(m.predict(x[:7], fit), np.full(7, target.mean()))
    q = m.predict_proba(x[:3], fit)
    assert q.shape == (3,) and np.all((q > 0) & (q < 1))
