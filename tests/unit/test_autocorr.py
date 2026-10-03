"""Unit tests for windowing and autocorrelation.

Reference values are hand-computed (stored in expected.json), not
produced by the code under test.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.lpc.autocorr import apply_window, autocorrelation


def test_autocorrelation_matches_hand_computed(expected):
    ref = expected["autocorr_handcheck"]
    x = np.array(ref["signal"])
    r = autocorrelation(x, ref["order"])
    # r[0]=1+4+9+16=30, r[1]=2+6+12=20, r[2]=3+8=11 (computed by hand)
    np.testing.assert_allclose(r, ref["r"], rtol=0, atol=1e-12)


def test_autocorrelation_zero_signal_is_zero_energy():
    r = autocorrelation(np.zeros(64), 4)
    assert r[0] == 0.0
    np.testing.assert_array_equal(r, np.zeros(5))


def test_autocorrelation_rejects_order_not_below_frame_length():
    with pytest.raises(ValueError, match="order"):
        autocorrelation(np.ones(8), 8)
    with pytest.raises(ValueError, match="order"):
        autocorrelation(np.ones(8), 0)


def test_autocorrelation_rejects_2d_input():
    with pytest.raises(ValueError, match="1-D"):
        autocorrelation(np.ones((4, 4)), 2)


def test_apply_window_rect_is_identity():
    x = np.random.default_rng(1).standard_normal(32)
    np.testing.assert_array_equal(apply_window(x, "rect"), x)


def test_apply_window_hann_tapers_endpoints():
    x = np.ones(31)  # odd length: the Hann peak hits exactly 1.0
    y = apply_window(x, "hann")
    assert y[0] == pytest.approx(0.0, abs=1e-17)
    assert y[len(y) // 2] == pytest.approx(1.0, abs=1e-15)


def test_apply_window_rejects_unknown_window():
    with pytest.raises(ValueError, match="unsupported window"):
        apply_window(np.ones(8), "blackman-harris-9000")
