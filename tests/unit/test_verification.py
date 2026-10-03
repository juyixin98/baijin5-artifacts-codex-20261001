"""Unit tests for the numerical verification module."""

from __future__ import annotations

import numpy as np
import pytest

from app.lpc.autocorr import autocorrelation
from app.lpc.levinson import levinson_durbin
from app.verification import (
    VERDICT_DEGRADED,
    VERDICT_LOSSLESS,
    VERDICT_UNCERTAIN,
    assess_reconstruction,
    cross_check_toeplitz,
    stability_from_reflections,
)


def test_assess_lossless_when_direct_error_small():
    x = np.linspace(-1, 1, 100)
    a = assess_reconstruction(x, x + 1e-13, np.full(100, 1e-13))
    assert a.lossless_confirmed
    assert a.verdict == VERDICT_LOSSLESS


def test_assess_uncertain_when_residual_small_but_error_large():
    """The core anti-pattern guard: a small residual alone must never be
    accepted as proof of lossless reconstruction."""
    rng = np.random.default_rng(7)
    x = rng.standard_normal(256)
    y = x + 0.1 * rng.standard_normal(256)  # 10% reconstruction error
    residual = 1e-12 * rng.standard_normal(256)  # deceptively small residual
    a = assess_reconstruction(x, y, residual)
    assert not a.lossless_confirmed
    assert a.verdict == VERDICT_UNCERTAIN
    assert any("small residual" in reason for reason in a.reasons)


def test_assess_degraded_when_both_errors_large():
    rng = np.random.default_rng(8)
    x = rng.standard_normal(128)
    a = assess_reconstruction(x, x + 0.5, rng.standard_normal(128))
    assert not a.lossless_confirmed
    assert a.verdict == VERDICT_DEGRADED


def test_assess_zero_signal_zero_reconstruction():
    x = np.zeros(64)
    a = assess_reconstruction(x, np.zeros(64), np.zeros(64))
    assert a.lossless_confirmed
    assert a.max_abs_error == 0.0


def test_assess_rejects_shape_mismatch():
    with pytest.raises(ValueError, match="shapes differ"):
        assess_reconstruction(np.zeros(4), np.zeros(5), np.zeros(4))


def test_cross_check_agrees_on_well_conditioned_input(noise):
    order = 10
    r = autocorrelation(noise[:256], order)
    res = levinson_durbin(r, order)
    agrees, dev, diag = cross_check_toeplitz(r, order, res.lpc)
    assert agrees
    assert dev < 1e-8
    assert diag is None


def test_cross_check_flags_tampered_coefficients(noise):
    order = 10
    r = autocorrelation(noise[:256], order)
    res = levinson_durbin(r, order)
    bad = res.lpc.copy()
    bad[1] += 0.01
    agrees, dev, diag = cross_check_toeplitz(r, order, bad)
    assert not agrees
    assert dev == pytest.approx(0.01, rel=1e-6)


def test_stability_from_reflections():
    stable, max_abs, diag = stability_from_reflections(np.array([0.3, -0.7, 0.1]))
    assert stable
    assert max_abs == pytest.approx(0.7)
    assert diag == ()

    stable, max_abs, diag = stability_from_reflections(np.array([0.3, 1.2]))
    assert not stable
    assert max_abs == pytest.approx(1.2)
    assert "unstable_reflection_coefficient" in diag
