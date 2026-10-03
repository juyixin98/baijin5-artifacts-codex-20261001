"""NLMS-specific numerics: energy regularisation, silence, stability bound."""

from __future__ import annotations

import numpy as np

from app.algorithms.lms import AdaptiveFilter, FilterSpec
from app.config import Settings
from app.fixtures.synth import silence_scenario


def make_nlms(mu=0.5, length=8, epsilon=1e-8):
    return AdaptiveFilter(
        FilterSpec("nlms", length, mu, epsilon), Settings()
    )


class TestEnergyRegularisation:
    def test_zero_reference_cannot_divide_by_zero(self):
        """Silent reference: denominator is exactly epsilon, update is zero
        because the buffer is all zeros — no NaN, weights stay put."""
        f = make_nlms(mu=1.9)  # aggressive step size to stress the guard
        sc = silence_scenario(n=256, filter_length=8)
        result = f.process_block(sc.reference, sc.desired)
        assert np.all(np.isfinite(result.outputs))
        assert np.all(np.isfinite(result.errors))
        np.testing.assert_array_equal(f.weights(), np.zeros(8))
        # With zero reference the output is zero, so residual == desired.
        np.testing.assert_allclose(result.errors, sc.desired, atol=1e-15)

    def test_tiny_reference_update_stays_bounded(self):
        """A near-zero reference must not explode: |delta_w| <= mu/eps * |e| * |x|."""
        f = make_nlms(mu=1.0, length=4, epsilon=1e-6)
        x = np.full(16, 1e-9)
        d = np.ones(16)
        result = f.process_block(x, d)
        assert np.all(np.isfinite(f.weights()))
        assert np.max(np.abs(f.weights())) <= 1.0 / 1e-6 * 1.0 * 1e-9 * 16 + 1e-12
        assert np.all(np.isfinite(result.errors))

    def test_silence_then_signal_recovers(self):
        """After a silent prefix the filter adapts normally."""
        f = make_nlms(mu=0.8, length=4)
        f.process_block(np.zeros(32), np.zeros(32))
        rng = np.random.default_rng(0)
        xs = rng.standard_normal(64)
        ds = 2.0 * xs  # plant = pure gain of 2, delay 0
        f.process_block(xs, ds)
        # Most recent tap should have moved toward the gain.
        assert f.weights()[-1] > 1.0


class TestStabilityBoundContract:
    def test_mu_two_is_rejected_not_clamped(self):
        import pytest

        from app.errors import InputValidationError

        with pytest.raises(InputValidationError):
            make_nlms(mu=2.0)

    def test_nlms_step_is_normalized_by_energy(self):
        """For a single-tap filter, one NLMS step with mu=1 exactly solves
        e=0 for the current regressor (up to epsilon)."""
        f = make_nlms(mu=1.0, length=1, epsilon=1e-12)
        f.process_block(np.array([3.0]), np.array([6.0]))
        # w = 0 + (1/(eps+9)) * 6 * 3 = 18/9 = 2 (approximately)
        assert abs(f.weights()[0] - 2.0) < 1e-10
