"""Tests for explicit time alignment."""

from __future__ import annotations

import numpy as np
import pytest

from app.errors import InputError
from app.signal_processing.alignment import apply_delay, estimate_delay

from .fixtures import KNOWN_FIR, make_response, white_excitation


def test_estimated_delay_recovers_known_lag():
    x = white_excitation(2000, seed=11)
    true_delay = 7
    y = make_response(x, KNOWN_FIR, delay=true_delay)
    assert estimate_delay(x, y, max_delay=50) == true_delay


def test_estimated_delay_zero_when_aligned():
    x = white_excitation(2000, seed=12)
    y = make_response(x, KNOWN_FIR, delay=0)
    assert estimate_delay(x, y, max_delay=50) == 0


def test_apply_delay_shifts_response_and_truncates():
    x = np.arange(10.0)
    y = np.arange(100.0, 110.0)
    x_al, y_al = apply_delay(x, y, 3)
    assert x_al.size == y_al.size == 7
    np.testing.assert_array_equal(y_al, y[3:])
    np.testing.assert_array_equal(x_al, x[:7])


def test_apply_delay_zero_is_identity():
    x = np.arange(5.0)
    y = np.arange(5.0, 10.0)
    x_al, y_al = apply_delay(x, y, 0)
    np.testing.assert_array_equal(x_al, x)
    np.testing.assert_array_equal(y_al, y)


def test_delay_out_of_range_rejected():
    with pytest.raises(InputError, match="delay"):
        apply_delay(np.ones(4), np.ones(4), 4)


def test_max_delay_out_of_range_rejected():
    with pytest.raises(InputError, match="max_delay"):
        estimate_delay(np.ones(10), np.ones(10), max_delay=10)
