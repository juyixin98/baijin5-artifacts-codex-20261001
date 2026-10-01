"""Weighted least squares engine tests, including failure categories."""
from __future__ import annotations

import numpy as np
import pytest

from app.core.contracts import FitError
from app.core.wls import weighted_least_squares


def test_recovers_exact_line_without_noise():
    rng = np.random.default_rng(0)
    d = rng.uniform(0.0, 1.0, 400)
    y = 5.0 - 2.0 * d                       # intercept 5, slope -2
    w = np.ones_like(d)
    res = weighted_least_squares(d, y, w)
    assert res.intercept == pytest.approx(5.0, abs=1e-9)
    assert res.slope == pytest.approx(-2.0, abs=1e-9)
    assert res.se_homoskedastic == pytest.approx(0.0, abs=1e-9)
    assert res.n == 400


def test_weights_change_fit_as_expected():
    # Fit y = 0 + 1*d on most points but outliers near d=0 with tiny weight.
    d = np.linspace(0.01, 1.0, 50)
    y = d.copy()
    d_corrupt = np.concatenate([d, [0.001, 0.002]])
    y_corrupt = np.concatenate([y, [100.0, 100.0]])
    w_heavy = np.ones(d_corrupt.size)
    w_heavy[-2:] = 1e-12
    res = weighted_least_squares(d_corrupt, y_corrupt, w_heavy)
    assert abs(res.intercept) < 0.5                # outliers ignored
    res_noweight = weighted_least_squares(
        d_corrupt, y_corrupt, np.ones(d_corrupt.size))
    assert abs(res_noweight.intercept) > 5.0       # outliers dominate unweighted


def test_rejects_fewer_than_three_positive_weights():
    d = np.array([0.1, 0.2, 0.3])
    y = np.array([1.0, 2.0, 3.0])
    w = np.array([1.0, 1.0, 0.0])
    with pytest.raises(FitError, match="not enough effective"):
        weighted_least_squares(d, y, w)


def test_rejects_rank_deficient_design():
    # all x identical -> rank 1
    d = np.array([0.5, 0.5, 0.5, 0.5])
    y = np.array([1.0, 2.0, 1.5, 1.8])
    with pytest.raises(FitError, match="rank-deficient"):
        weighted_least_squares(d, y, np.ones_like(d))


def test_rejects_ill_conditioned_collinear_design():
    # Points spanning 1e-7 with O(1) outcomes -> numerically singular after
    # scaling; must raise rather than return spuriously tiny SEs.
    d = np.array([0.3, 0.3 + 1e-9, 0.3 + 2e-9])
    y = np.array([1.0, 1.2, 0.8])
    with pytest.raises(FitError, match="ill-conditioned"):
        weighted_least_squares(d, y, np.ones_like(d))


def test_rejects_negative_weights_and_nonfinite():
    d = np.array([0.1, 0.2, 0.3])
    y = np.array([1.0, 2.0, 3.0])
    with pytest.raises(FitError, match="non-negative"):
        weighted_least_squares(d, y, np.array([1.0, -1.0, 1.0]))
    with pytest.raises(FitError, match="non-finite"):
        weighted_least_squares(np.array([0.1, np.nan, 0.3]), y, np.ones(3))


def test_se_decreases_with_sample_size():
    rng = np.random.default_rng(42)
    ses = []
    for n in (100, 1000, 5000):
        d = rng.uniform(0.0, 1.0, n)
        y = d + rng.normal(0.0, 1.0, n)
        ses.append(weighted_least_squares(d, y, np.ones(n)).se_hc2)
    assert ses[0] > ses[1] > ses[2]
