"""Estimator tests against known-answer fixtures.

Reference answers come from hand-specified fixture taps, numpy.convolve
synthesis, and an independent normal-equations solve via scipy — not
from the estimator's own code path.
"""

import numpy as np
import pytest
import scipy.linalg

from fir_backend.contracts import EstimateParams
from fir_backend.convolution import convolution_matrix
from fir_backend.errors import ComputationError, InputValidationError
from fir_backend.estimator import estimate_fir
from fir_backend.fixtures import make_fixture


def _params(**overrides):
    base = dict(model_order=8, delay=0, regularization=0.0, holdout_fraction=0.25)
    base.update(overrides)
    return EstimateParams(**base)


def test_clean_fixture_recovers_coefficients_exactly():
    fixture = make_fixture("clean", fir="decay8")
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    h_est = result.coefficients_array()
    np.testing.assert_allclose(h_est, fixture.true_coefficients, atol=1e-8)
    assert result.diagnostics.identifiable
    assert result.diagnostics.rank == 8
    assert result.diagnostics.train_rmse < 1e-8
    assert result.diagnostics.holdout_rmse < 1e-8


def test_coefficients_match_independent_normal_equations():
    # Independent reference: build the Gram system in the test and solve
    # it with scipy — a different numerical path from the estimator.
    fixture = make_fixture("noisy", fir="decay8", noise_std=0.05)
    params = _params(regularization=1e-3)
    result = estimate_fir(fixture.excitation, fixture.response, params)
    n_train = result.diagnostics.n_train
    design = convolution_matrix(fixture.excitation, 8, mode="zero_pad")[:n_train]
    gram = design.T @ design + 1e-3 * np.eye(8)
    rhs = design.T @ fixture.response[:n_train]
    h_ref = scipy.linalg.solve(gram, rhs, assume_a="pos")
    np.testing.assert_allclose(result.coefficients_array(), h_ref, atol=1e-6)


def test_noisy_fixture_coefficient_and_holdout_accuracy():
    noise_std = 0.05
    fixture = make_fixture("noisy", fir="decay8", noise_std=noise_std)
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    h_est = result.coefficients_array()
    assert np.max(np.abs(h_est - fixture.true_coefficients)) < 0.05
    diag = result.diagnostics
    # Both errors live near the noise floor; neither is trivially zero.
    assert 0.5 * noise_std < diag.train_rmse < 2.0 * noise_std
    assert 0.5 * noise_std < diag.holdout_rmse < 2.0 * noise_std


def test_train_and_holdout_are_separate_ranges():
    fixture = make_fixture("noisy", fir="decay8")
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    diag = result.diagnostics
    assert diag.n_train + diag.n_holdout == diag.n_samples_aligned
    assert diag.n_holdout > 0
    assert diag.holdout_rmse is not None
    # Disjoint ranges with independent noise: the two errors must differ.
    assert diag.train_rmse != diag.holdout_rmse


def test_holdout_disabled_reports_none():
    fixture = make_fixture("noisy", fir="decay8")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(holdout_fraction=0.0)
    )
    assert result.diagnostics.n_holdout == 0
    assert result.diagnostics.holdout_rmse is None


def test_narrowband_excitation_is_reported_unidentifiable():
    fixture = make_fixture("narrowband", fir="decay8")
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    diag = result.diagnostics
    assert not diag.identifiable
    # A pure sinusoid excites exactly 2 of the 8 tap directions.
    assert diag.effective_rank == 2
    assert diag.effective_rank < diag.model_order
    assert any("effective rank" in r for r in diag.unidentifiable_reasons)


def test_white_excitation_has_full_effective_rank():
    fixture = make_fixture("clean", fir="decay8")
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    assert result.diagnostics.effective_rank == 8


def test_white_excitation_is_identifiable():
    fixture = make_fixture("clean", fir="decay8")
    result = estimate_fir(fixture.excitation, fixture.response, _params())
    assert result.diagnostics.identifiable
    assert result.diagnostics.unidentifiable_reasons == ()


def test_regularization_shrinks_coefficient_norm_monotonically():
    fixture = make_fixture("noisy", fir="decay8", noise_std=0.05)
    lambdas = [0.0, 1e-4, 1e-2, 1.0, 100.0]
    norms = []
    for lam in lambdas:
        result = estimate_fir(
            fixture.excitation, fixture.response, _params(regularization=lam)
        )
        norms.append(float(np.linalg.norm(result.coefficients_array())))
    for smaller, larger in zip(norms, norms[1:]):
        assert larger < smaller


def test_huge_regularization_drives_coefficients_to_zero():
    fixture = make_fixture("noisy", fir="decay8")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(regularization=1e12)
    )
    assert np.linalg.norm(result.coefficients_array()) < 1e-6


def test_mild_regularization_barely_moves_clean_estimate():
    fixture = make_fixture("clean", fir="decay8")
    result = estimate_fir(
        fixture.excitation, fixture.response, _params(regularization=1e-8)
    )
    np.testing.assert_allclose(
        result.coefficients_array(), fixture.true_coefficients, atol=1e-4
    )


def test_delay_misalignment_breaks_recovery_without_explicit_delay():
    fixture = make_fixture("delayed", fir="decay8", delay=5)
    naive = estimate_fir(fixture.excitation, fixture.response, _params())
    error = np.max(np.abs(naive.coefficients_array() - fixture.true_coefficients))
    assert error > 0.1


def test_explicit_delay_restores_recovery():
    fixture = make_fixture("delayed", fir="decay8", delay=5)
    result = estimate_fir(fixture.excitation, fixture.response, _params(delay=5))
    np.testing.assert_allclose(
        result.coefficients_array(), fixture.true_coefficients, atol=1e-8
    )
    assert result.diagnostics.n_samples_aligned == 512 - 5


def test_lowpass4_fixture_recovers_literal_taps():
    fixture = make_fixture("clean", fir="lowpass4", n_samples=256)
    result = estimate_fir(fixture.excitation, fixture.response, _params(model_order=4))
    np.testing.assert_allclose(
        result.coefficients_array(), [0.4, 0.3, 0.2, 0.1], atol=1e-10
    )


def test_mismatched_input_lengths_raise_input_error():
    with pytest.raises(InputValidationError):
        estimate_fir(np.ones(64), np.ones(65), _params())


def test_solver_failure_is_computation_error(monkeypatch):
    fixture = make_fixture("clean", fir="decay8")

    def boom(*args, **kwargs):
        raise np.linalg.LinAlgError("forced failure")

    monkeypatch.setattr(np.linalg, "lstsq", boom)
    with pytest.raises(ComputationError):
        estimate_fir(fixture.excitation, fixture.response, _params())
