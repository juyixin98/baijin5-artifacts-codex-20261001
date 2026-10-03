"""Tests for the regularized least-squares FIR estimator.

Reference answers: known true coefficients, an independent normal-equations
solve via numpy on a tiny problem, and analytic facts about ridge shrinkage.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.errors import (
    ComputationError,
    IdentifiabilityError,
    InputError,
    ResourceExhaustedError,
)
from app.signal_processing.estimator import estimate_fir

from .fixtures import KNOWN_FIR, make_response, narrowband_excitation, white_excitation

ORDER = len(KNOWN_FIR)


def test_noiseless_recovery_matches_true_coefficients():
    x = white_excitation(2000, seed=21)
    y = make_response(x, KNOWN_FIR)
    result = estimate_fir(
        x, y, order=ORDER, regularization=1e-10, holdout_fraction=0.25
    )
    np.testing.assert_allclose(result.coefficients, KNOWN_FIR, atol=1e-6)
    assert result.train_metrics["mse"] < 1e-12
    assert result.holdout_metrics is not None
    assert result.holdout_metrics["mse"] < 1e-12
    assert result.identifiable


def test_noisy_recovery_close_and_holdout_reports_noise_floor():
    x = white_excitation(8000, seed=22)
    noise_std = 0.1
    y = make_response(x, KNOWN_FIR, noise_std=noise_std, seed=23)
    result = estimate_fir(
        x, y, order=ORDER, regularization=1e-8, holdout_fraction=0.25
    )
    np.testing.assert_allclose(result.coefficients, KNOWN_FIR, atol=0.05)
    # held-out prediction error should sit near the noise variance
    assert result.holdout_metrics["mse"] == pytest.approx(noise_std**2, rel=0.3)
    # train and holdout errors are separate, non-empty reports
    assert result.train_metrics["n_samples"] > 0
    assert result.holdout_metrics["n_samples"] > 0


def test_ridge_solution_matches_independent_normal_equations():
    rng = np.random.default_rng(31)
    x = rng.standard_normal(300)
    y = make_response(x, KNOWN_FIR)
    lam = 1e-3
    result = estimate_fir(
        x, y, order=ORDER, regularization=lam, holdout_fraction=0.0
    )
    # independent reference: build the Toeplitz matrix and solve the normal
    # equations with numpy directly (no application code involved).
    n_rows = x.size - ORDER + 1
    ref_matrix = np.zeros((n_rows, ORDER))
    for i in range(n_rows):
        for k in range(ORDER):
            ref_matrix[i, k] = x[i + ORDER - 1 - k]
    ref_obs = y[ORDER - 1 :]
    gram = ref_matrix.T @ ref_matrix + lam * np.eye(ORDER)
    ref_h = np.linalg.solve(gram, ref_matrix.T @ ref_obs)
    np.testing.assert_allclose(result.coefficients, ref_h, rtol=1e-9, atol=1e-9)


def test_regularization_shrinks_coefficient_norm_monotonically():
    x = white_excitation(3000, seed=24)
    y = make_response(x, KNOWN_FIR, noise_std=0.05, seed=25)
    norms = []
    for lam in (1e-6, 1e-2, 1e2, 1e6):
        result = estimate_fir(x, y, order=ORDER, regularization=lam)
        norms.append(np.linalg.norm(result.coefficients))
    assert all(a > b for a, b in zip(norms, norms[1:]))
    # strong regularization drives the estimate far from the truth
    assert norms[-1] < 0.1 * np.linalg.norm(KNOWN_FIR)


def test_rank_deficient_excitation_reported():
    x = narrowband_excitation(2000, frequency=0.05, n_tones=1)
    y = make_response(x, KNOWN_FIR)
    result = estimate_fir(x, y, order=ORDER, regularization=1e-4)
    assert not result.identifiable
    assert result.identifiability.rank == 2
    assert any("rank-deficient" in w for w in result.warnings)


def test_rank_deficient_unregularized_solve_fails_as_computation_error():
    x = narrowband_excitation(2000, frequency=0.05, n_tones=1)
    y = make_response(x, KNOWN_FIR)
    with pytest.raises(ComputationError, match="singular|rank"):
        estimate_fir(x, y, order=ORDER, regularization=0.0)


def test_require_identifiable_raises_identifiability_error():
    x = narrowband_excitation(2000, frequency=0.05, n_tones=1)
    y = make_response(x, KNOWN_FIR)
    with pytest.raises(IdentifiabilityError, match="degenerate"):
        estimate_fir(
            x, y, order=ORDER, regularization=1e-4, require_identifiable=True
        )


def test_delay_misalignment_degrades_and_explicit_delay_restores():
    x = white_excitation(4000, seed=26)
    true_delay = 5
    y = make_response(x, KNOWN_FIR, delay=true_delay)
    misaligned = estimate_fir(x, y, order=ORDER, regularization=1e-8, delay=0)
    aligned = estimate_fir(
        x, y, order=ORDER, regularization=1e-8, delay=true_delay
    )
    estimated = estimate_fir(
        x, y, order=ORDER, regularization=1e-8, estimate_delay_flag=True
    )
    assert aligned.holdout_metrics["mse"] < 1e-12
    assert misaligned.holdout_metrics["mse"] > 100 * aligned.holdout_metrics["mse"]
    assert estimated.delay == true_delay
    np.testing.assert_allclose(estimated.coefficients, KNOWN_FIR, atol=1e-6)


def test_identical_excitation_and_response_refused():
    x = white_excitation(500, seed=27)
    with pytest.raises(InputError, match="itself"):
        estimate_fir(x, x.copy(), order=ORDER)


def test_non_finite_input_rejected():
    x = white_excitation(500, seed=28)
    y = make_response(x, KNOWN_FIR)
    y[10] = np.nan
    with pytest.raises(InputError, match="finite"):
        estimate_fir(x, y, order=ORDER)


def test_length_mismatch_rejected():
    with pytest.raises(InputError, match="length"):
        estimate_fir(np.ones(100), np.ones(99), order=4)


def test_invalid_order_rejected():
    x = white_excitation(200, seed=29)
    y = make_response(x, KNOWN_FIR)
    with pytest.raises(InputError, match="order"):
        estimate_fir(x, y, order=0)
    with pytest.raises(InputError, match="order"):
        estimate_fir(x, y, order=len(x) + 1)


def test_negative_regularization_rejected():
    x = white_excitation(200, seed=30)
    y = make_response(x, KNOWN_FIR)
    with pytest.raises(InputError, match="regularization"):
        estimate_fir(x, y, order=ORDER, regularization=-1.0)


def test_resource_limits_enforced(small_config):
    x = white_excitation(5000, seed=31)  # exceeds small_config.max_samples
    y = make_response(x, KNOWN_FIR)
    with pytest.raises(ResourceExhaustedError, match="samples"):
        estimate_fir(x, y, order=ORDER, config=small_config)
    x2 = white_excitation(1000, seed=32)
    y2 = make_response(x2, KNOWN_FIR)
    with pytest.raises(ResourceExhaustedError, match="order"):
        estimate_fir(x2, y2, order=100, config=small_config)


def test_holdout_fraction_zero_disables_holdout():
    x = white_excitation(1000, seed=33)
    y = make_response(x, KNOWN_FIR)
    result = estimate_fir(x, y, order=ORDER, holdout_fraction=0.0)
    assert result.holdout_metrics is None
    assert result.train_metrics["n_samples"] == 1000 - ORDER + 1


def test_zero_pad_boundary_recovers_coefficients():
    x = white_excitation(1500, seed=34)
    y = make_response(x, KNOWN_FIR)
    result = estimate_fir(
        x, y, order=ORDER, regularization=1e-10, boundary="zero_pad"
    )
    np.testing.assert_allclose(result.coefficients, KNOWN_FIR, atol=1e-6)
