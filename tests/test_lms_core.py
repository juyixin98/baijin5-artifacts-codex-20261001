"""Single-step and contract tests for the LMS/NLMS core.

The expected values in the hand-computed tests are derived from the
textbook equations with literal arithmetic — they are independent of the
implementation under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.algorithms.lms import AdaptiveFilter, FilterSpec
from app.config import Settings
from app.errors import ComputationError, InputValidationError


def make_filter(algorithm="lms", mu=0.1, length=2, epsilon=1e-8):
    spec = FilterSpec(algorithm=algorithm, filter_length=length, mu=mu, epsilon=epsilon)
    return AdaptiveFilter(spec, Settings())


def preset(f: AdaptiveFilter, weights, buffer):
    f._w = np.asarray(weights, dtype=np.float64)
    f._buf = np.asarray(buffer, dtype=np.float64)


class TestHandComputedSingleStep:
    """L=2, w=[0.3, -0.1], buffer=[1.0, 2.0], x=3.0, d=1.0.

    After shifting: buffer=[2.0, 3.0].
    y = 0.3*2.0 + (-0.1)*3.0 = 0.3 ; e = 1.0 - 0.3 = 0.7
    """

    def test_lms_single_step(self):
        f = make_filter(algorithm="lms", mu=0.1)
        preset(f, [0.3, -0.1], [1.0, 2.0])
        y, e = f.step(3.0, 1.0)
        assert y == pytest.approx(0.3, abs=1e-15)
        assert e == pytest.approx(0.7, abs=1e-15)
        # w = [0.3 + 0.1*0.7*2.0, -0.1 + 0.1*0.7*3.0] = [0.44, 0.11]
        np.testing.assert_allclose(f.weights(), [0.44, 0.11], atol=1e-15)
        np.testing.assert_allclose(f.buffer(), [2.0, 3.0], atol=1e-15)

    def test_nlms_single_step(self):
        f = make_filter(algorithm="nlms", mu=0.1, epsilon=0.5)
        preset(f, [0.3, -0.1], [1.0, 2.0])
        y, e = f.step(3.0, 1.0)
        assert y == pytest.approx(0.3, abs=1e-15)
        assert e == pytest.approx(0.7, abs=1e-15)
        # denom = 0.5 + 2^2 + 3^2 = 13.5
        # w = [0.3 + (0.1/13.5)*0.7*2.0, -0.1 + (0.1/13.5)*0.7*3.0]
        expected = [0.3 + (0.1 / 13.5) * 1.4, -0.1 + (0.1 / 13.5) * 2.1]
        np.testing.assert_allclose(f.weights(), expected, atol=1e-15)

    def test_frozen_sample_filters_but_does_not_adapt(self):
        f = make_filter(algorithm="nlms", mu=0.9, epsilon=0.5)
        preset(f, [0.3, -0.1], [1.0, 2.0])
        y, e = f.step(3.0, 1.0, freeze=True)
        assert y == pytest.approx(0.3, abs=1e-15)
        assert e == pytest.approx(0.7, abs=1e-15)
        np.testing.assert_allclose(f.weights(), [0.3, -0.1], atol=1e-15)
        np.testing.assert_allclose(f.buffer(), [2.0, 3.0], atol=1e-15)
        assert f.next_index == 1


class TestLearningRateBounds:
    @pytest.mark.parametrize("mu", [0.0, -0.1, 1.0 + 1e-12, 2.0])
    def test_lms_mu_out_of_range_rejected(self, mu):
        with pytest.raises(InputValidationError) as exc:
            make_filter(algorithm="lms", mu=mu)
        assert exc.value.reason == "mu_range"

    def test_lms_mu_at_upper_bound_accepted(self):
        make_filter(algorithm="lms", mu=1.0)  # inclusive ceiling

    @pytest.mark.parametrize("mu", [0.0, -0.5, 2.0, 2.5])
    def test_nlms_mu_out_of_range_rejected(self, mu):
        with pytest.raises(InputValidationError) as exc:
            make_filter(algorithm="nlms", mu=mu)
        assert exc.value.reason == "mu_range"

    def test_nlms_mu_just_below_two_accepted(self):
        make_filter(algorithm="nlms", mu=2.0 - 1e-9)

    def test_epsilon_must_be_positive(self):
        with pytest.raises(InputValidationError) as exc:
            make_filter(algorithm="nlms", epsilon=0.0)
        assert exc.value.reason == "epsilon_range"


class TestInputValidation:
    def test_length_mismatch(self):
        f = make_filter()
        with pytest.raises(InputValidationError) as exc:
            f.process_block(np.zeros(4), np.zeros(3))
        assert exc.value.reason == "length_mismatch"

    def test_empty_block(self):
        f = make_filter()
        with pytest.raises(InputValidationError) as exc:
            f.process_block(np.array([]), np.array([]))
        assert exc.value.reason == "empty_block"

    @pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
    def test_non_finite_input_rejected(self, bad):
        f = make_filter()
        with pytest.raises(InputValidationError) as exc:
            f.process_block(np.array([1.0, bad]), np.array([1.0, 1.0]))
        assert exc.value.reason == "non_finite_input"
        assert f.next_index == 0  # nothing consumed


class TestComputationFailure:
    def test_diverged_block_rolls_back_state(self):
        # LMS with mu=1.0, enormous reference samples and a non-zero desired
        # signal diverges to inf within a few steps.
        f = make_filter(algorithm="lms", mu=1.0, length=4)
        huge = np.full(64, 1e200)
        with np.errstate(over="ignore", invalid="ignore"):
            with pytest.raises(ComputationError) as exc:
                f.process_block(huge, np.ones(64))
        assert exc.value.reason == "non_finite_state"
        # State preserved: caller may retry the same index.
        assert f.next_index == 0
        np.testing.assert_array_equal(f.weights(), np.zeros(4))
