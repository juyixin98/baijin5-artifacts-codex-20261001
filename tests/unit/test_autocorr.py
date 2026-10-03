"""Autocorrelation: hand-computed values and an independent FFT reference."""
from __future__ import annotations

import numpy as np
import pytest

from app.lpc.autocorr import autocorrelation


def test_hand_computed_small_frame():
    # r[k] = sum_n x[n] x[n-k] for x = [1, 2, 3]:
    # r[0] = 1+4+9 = 14, r[1] = 2+6 = 8, r[2] = 3
    r = autocorrelation(np.array([1.0, 2.0, 3.0]), 2)
    assert r.tolist() == [14.0, 8.0, 3.0]


def test_zero_signal_gives_zero_autocorrelation():
    r = autocorrelation(np.zeros(16), 4)
    assert np.all(r == 0.0)


def test_matches_independent_fft_reference():
    rng = np.random.default_rng(7)
    x = rng.standard_normal(200)
    order = 12
    # Independent reference: autocorrelation via FFT convolution theorem.
    nfft = 1 << (2 * x.size - 1).bit_length()
    spectrum = np.fft.rfft(x, nfft)
    r_ref = np.fft.irfft(spectrum * np.conj(spectrum), nfft)[: order + 1]
    r = autocorrelation(x, order)
    np.testing.assert_allclose(r, r_ref, rtol=1e-10, atol=1e-10)


def test_order_must_be_smaller_than_frame():
    with pytest.raises(ValueError):
        autocorrelation(np.ones(8), 8)
