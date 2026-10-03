"""Time alignment tests: explicit delay application and diagnostic estimation."""

import numpy as np
import pytest

from fir_backend.alignment import align_pair, estimate_delay
from fir_backend.contracts import SampleBlock
from fir_backend.errors import InputValidationError
from fir_backend.fixtures import make_fixture


def test_positive_delay_drops_response_head_and_excitation_tail():
    x = np.arange(10.0)
    y = np.arange(100.0, 110.0)
    aligned = align_pair(SampleBlock(excitation=x, response=y), 3)
    assert aligned.n_samples == 7
    np.testing.assert_array_equal(aligned.response, y[3:])
    np.testing.assert_array_equal(aligned.excitation, x[:7])


def test_negative_delay_drops_excitation_head_and_response_tail():
    x = np.arange(10.0)
    y = np.arange(100.0, 110.0)
    aligned = align_pair(SampleBlock(excitation=x, response=y), -2)
    assert aligned.n_samples == 8
    np.testing.assert_array_equal(aligned.excitation, x[2:])
    np.testing.assert_array_equal(aligned.response, y[:8])


def test_zero_delay_returns_block_unchanged():
    block = SampleBlock(excitation=np.ones(5), response=np.zeros(5))
    aligned = align_pair(block, 0)
    assert aligned is block


def test_oversized_delay_rejected():
    block = SampleBlock(excitation=np.ones(5), response=np.ones(5))
    with pytest.raises(InputValidationError):
        align_pair(block, 5)
    with pytest.raises(InputValidationError):
        align_pair(block, -5)


def test_estimate_delay_recovers_known_lag():
    fixture = make_fixture("delayed", delay=5)
    assert estimate_delay(fixture.excitation, fixture.response, max_delay=32) == 5


def test_estimate_delay_zero_for_aligned_signals():
    fixture = make_fixture("clean")
    assert estimate_delay(fixture.excitation, fixture.response, max_delay=32) == 0


def test_estimate_delay_respects_max_delay_window():
    fixture = make_fixture("delayed", delay=5)
    # A window tighter than the true lag cannot report it.
    found = estimate_delay(fixture.excitation, fixture.response, max_delay=2)
    assert -2 <= found <= 2
