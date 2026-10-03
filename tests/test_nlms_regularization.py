"""NLMS energy regularization: silent and near-silent references."""

from __future__ import annotations

import numpy as np
import pytest

from app.dsp.lms import AdaptiveFilter


def test_zero_reference_never_divides_by_zero() -> None:
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.9, eps=1e-8)
    n = 128
    result = flt.process_block(np.zeros(n), np.ones(n))  # primary active, ref silent

    assert np.all(np.isfinite(result.output))
    assert np.all(np.isfinite(result.error))
    # Zero buffer => update term is exactly zero => weights stay at init.
    np.testing.assert_array_equal(flt.weights, np.zeros(4))
    # Output of a zero-weight filter over a zero reference is exactly 0.
    np.testing.assert_array_equal(result.output, np.zeros(n))
    np.testing.assert_array_equal(result.error, np.ones(n))
    # Regularization engaged: denominator bottomed out at eps, never 0.
    assert result.min_denominator == pytest.approx(1e-8)


def test_near_silent_reference_stays_finite() -> None:
    flt = AdaptiveFilter(filter_len=2, algorithm="nlms", mu=1.5, eps=1e-4)
    ref = np.full(64, 1e-9)  # energy ~1e-18 << eps
    pri = np.ones(64)
    result = flt.process_block(ref, pri)

    assert np.all(np.isfinite(flt.weights))
    assert result.min_denominator == pytest.approx(1e-4 + 2e-18, rel=1e-6)
    # With denominator dominated by eps, the update is heavily damped.
    assert np.max(np.abs(flt.weights)) < 1e-3


def test_weights_untouched_during_silence_mid_stream() -> None:
    flt = AdaptiveFilter(filter_len=4, algorithm="nlms", mu=0.5)
    rng = np.random.default_rng(3)
    flt.process_block(rng.standard_normal(64), rng.standard_normal(64))
    # Flush the tap buffer: the first filter_len silent samples still see
    # old reference samples in the buffer, so silence only guarantees a
    # frozen filter once the buffer is fully zeroed.
    flt.process_block(np.zeros(8), np.zeros(8))
    converged = flt.weights.copy()

    flt.process_block(np.zeros(64), rng.standard_normal(64))
    np.testing.assert_array_equal(flt.weights, converged)
