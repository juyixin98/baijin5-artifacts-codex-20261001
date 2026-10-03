"""Single-step and block-level numerical contract of the LMS/NLMS core.

Reference answers in this module are hand-derived constants and an
independent re-implementation written with the opposite buffer
convention — none of them call the core under test.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from app.dsp.lms import AdaptiveFilter, freeze_mask_from_intervals
from app.errors import ComputationError, InputValidationError, ResourceExhaustedError

logger = logging.getLogger("opp506.tests")


def _preload(flt: AdaptiveFilter, weights: list[float], buffer: list[float]) -> None:
    flt.weights = np.asarray(weights, dtype=np.float64)
    flt.buffer = np.asarray(buffer, dtype=np.float64)


def test_lms_single_step_matches_hand_computed_reference() -> None:
    # Hand derivation (buffer index 0 = newest):
    #   pre-state: w = [0.5, -0.25], x_buf = [1.0, 2.0]
    #   new sample x = 3.0 -> buffer [3.0, 1.0]
    #   y = 0.5*3.0 + (-0.25)*1.0 = 1.25
    #   e = 2.0 - 1.25 = 0.75
    #   w' = w + 0.1 * 0.75 * [3.0, 1.0] = [0.725, -0.175]
    flt = AdaptiveFilter(filter_len=2, algorithm="lms", mu=0.1)
    _preload(flt, [0.5, -0.25], [1.0, 2.0])

    result = flt.step(3.0, 2.0)

    assert result.y == pytest.approx(1.25, abs=1e-15)
    assert result.e == pytest.approx(0.75, abs=1e-15)
    assert result.adapted is True
    np.testing.assert_allclose(flt.weights, [0.725, -0.175], atol=1e-15)
    logger.info(
        "lms single step verified: run=hand-ref-1 y=%.17g e=%.17g w=%s",
        result.y, result.e, flt.weights.tolist(),
    )


def test_nlms_single_step_matches_hand_computed_reference() -> None:
    # Same pre-state; NLMS with mu=0.5, eps=1e-8:
    #   energy = 3^2 + 1^2 = 10, denominator = 10 + 1e-8
    #   gain = 0.5 * 0.75 / (10 + 1e-8)
    #   w' = [0.5 + 3*gain, -0.25 + 1*gain]
    flt = AdaptiveFilter(filter_len=2, algorithm="nlms", mu=0.5, eps=1e-8)
    _preload(flt, [0.5, -0.25], [1.0, 2.0])

    result = flt.step(3.0, 2.0)

    gain = 0.5 * 0.75 / (10.0 + 1e-8)
    expected = np.array([0.5 + 3.0 * gain, -0.25 + gain])
    assert result.denominator == pytest.approx(10.0 + 1e-8, rel=1e-12)
    np.testing.assert_allclose(flt.weights, expected, rtol=1e-12, atol=1e-15)


def test_output_uses_pre_update_weights() -> None:
    # If the update were applied before the output, y would equal d (e=0)
    # for a one-tap filter after the first update; the fixed order must
    # produce y from the OLD weights instead.
    flt = AdaptiveFilter(filter_len=1, algorithm="lms", mu=0.9)
    flt.weights = np.array([0.0])
    result = flt.step(2.0, 1.0)
    assert result.y == 0.0  # pre-update weight was 0
    assert result.e == 1.0
    assert flt.weights[0] == pytest.approx(0.9 * 1.0 * 2.0)


def test_frozen_samples_leave_weights_bit_identical() -> None:
    flt = AdaptiveFilter(filter_len=3, algorithm="nlms", mu=0.8)
    rng = np.random.default_rng(0)
    ref = rng.standard_normal(64)
    pri = rng.standard_normal(64)
    freeze = np.zeros(64, dtype=bool)
    freeze[10:40] = True

    result = flt.process_block(ref, pri, freeze)

    assert result.adapted_samples == 64 - 30
    assert result.frozen_samples == 30

    # Replay only the frozen window on a fresh filter with the converged
    # state: weights must not move at all while frozen.
    before = flt.weights.copy()
    flt.process_block(ref[10:40], pri[10:40], np.ones(30, dtype=bool))
    np.testing.assert_array_equal(flt.weights, before)


def test_block_processing_matches_independent_loop() -> None:
    # Independent reference implementation: oldest-first buffer, explicit
    # python loop, written here in the test — not imported from app.dsp.
    rng = np.random.default_rng(7)
    n, length, mu, eps = 200, 5, 0.3, 1e-6
    ref = rng.standard_normal(n)
    pri = rng.standard_normal(n)

    w = np.zeros(length)
    buf = np.zeros(length)  # buf[0] = oldest here (opposite convention)
    expected_e = np.empty(n)
    for i in range(n):
        buf[:-1] = buf[1:]
        buf[-1] = ref[i]
        x = buf[::-1]  # newest-first view
        y = float(w @ x)
        expected_e[i] = pri[i] - y
        w = w + mu * expected_e[i] * x / (eps + float(x @ x))

    flt = AdaptiveFilter(filter_len=length, algorithm="nlms", mu=mu, eps=eps)
    result = flt.process_block(ref, pri)

    np.testing.assert_allclose(result.error, expected_e, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(flt.weights, w, rtol=1e-12, atol=1e-12)


def test_invalid_configuration_rejected_as_input_error() -> None:
    with pytest.raises(InputValidationError):
        AdaptiveFilter(filter_len=0, algorithm="lms", mu=0.1)
    with pytest.raises(InputValidationError):
        AdaptiveFilter(filter_len=2, algorithm="lms", mu=0.0)  # below range
    with pytest.raises(InputValidationError):
        AdaptiveFilter(filter_len=2, algorithm="lms", mu=2.0)  # at/above bound
    with pytest.raises(InputValidationError):
        AdaptiveFilter(filter_len=2, algorithm="nlms", mu=0.5, eps=0.0)
    with pytest.raises(InputValidationError):
        AdaptiveFilter(filter_len=2, algorithm="rls", mu=0.1)


def test_non_finite_sample_rejected() -> None:
    flt = AdaptiveFilter(filter_len=2, algorithm="lms", mu=0.1)
    with pytest.raises(InputValidationError):
        flt.step(float("nan"), 1.0)
    with pytest.raises(InputValidationError):
        flt.step(1.0, float("inf"))


def test_block_contract_violations() -> None:
    flt = AdaptiveFilter(filter_len=2, algorithm="lms", mu=0.1)
    with pytest.raises(InputValidationError):
        flt.process_block(np.zeros(4), np.zeros(5))  # length mismatch
    with pytest.raises(InputValidationError):
        flt.process_block(np.array([]), np.array([]))  # empty
    with pytest.raises(InputValidationError):
        flt.process_block(np.array([np.nan, 1.0]), np.zeros(2))  # non-finite
    with pytest.raises(InputValidationError):
        flt.process_block(np.zeros(3), np.zeros(3), np.zeros(2, dtype=bool))


def test_oversized_block_reported_as_resource_exhaustion() -> None:
    from app.config import Settings

    small = Settings(max_block_samples=8)
    flt = AdaptiveFilter(filter_len=2, algorithm="lms", mu=0.1, settings=small)
    with pytest.raises(ResourceExhaustedError):
        flt.process_block(np.zeros(16), np.zeros(16))


def test_numerical_blowup_reported_as_computation_failure() -> None:
    # mu is bounded, but pathologically large finite samples can still
    # overflow the update; that must surface as computation_failed, not
    # as silent inf weights.
    flt = AdaptiveFilter(filter_len=1, algorithm="lms", mu=1.9)
    with pytest.raises(ComputationError):
        flt.step(1e200, 1e200)


def test_freeze_interval_validation() -> None:
    with pytest.raises(InputValidationError):
        freeze_mask_from_intervals(10, [(5, 5)])  # empty interval
    with pytest.raises(InputValidationError):
        freeze_mask_from_intervals(10, [(8, 12)])  # out of bounds
    with pytest.raises(InputValidationError):
        freeze_mask_from_intervals(10, [(4, 8), (6, 9)])  # overlapping
    mask = freeze_mask_from_intervals(10, [(0, 2), (7, 10)])
    assert mask.sum() == 5
    assert mask[0] and mask[1] and mask[7] and not mask[2]
