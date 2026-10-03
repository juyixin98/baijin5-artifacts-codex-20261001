"""Unit tests for analysis/synthesis filters and state correspondence."""

from __future__ import annotations

import numpy as np
import pytest

from app.lpc.filters import (
    analysis_filter,
    analysis_state_from_history,
    synthesis_filter,
    synthesis_state_from_history,
)


def _stable_lpc() -> np.ndarray:
    return np.array([1.0, -0.5, 0.25, -0.1])


def test_zero_state_roundtrip_is_exact():
    rng = np.random.default_rng(3)
    x = rng.standard_normal(200)
    a = _stable_lpc()
    e, _ = analysis_filter(x, a)
    y, _ = synthesis_filter(e, a)
    np.testing.assert_allclose(y, x, rtol=0, atol=1e-12)


def test_streaming_equals_one_shot_with_carried_state():
    """Splitting a signal into frames must give identical residuals and
    reconstructions when the filter state is carried across frames."""
    rng = np.random.default_rng(4)
    x = rng.standard_normal(300)
    a = _stable_lpc()

    e_full, _ = analysis_filter(x, a)
    y_full, _ = synthesis_filter(e_full, a)

    e1, zf_a = analysis_filter(x[:100], a)
    e2, zf_a = analysis_filter(x[100:], a, zi=zf_a)
    np.testing.assert_allclose(np.concatenate([e1, e2]), e_full, atol=1e-14)

    y1, zf_s = synthesis_filter(e1, a)
    y2, zf_s = synthesis_filter(e2, a, zi=zf_s)
    y_streamed = np.concatenate([y1, y2])
    np.testing.assert_allclose(y_streamed, y_full, atol=1e-14)
    np.testing.assert_allclose(y_streamed, x, atol=1e-12)


def test_history_derived_states_match_carried_states():
    """States built from raw sample history (lfiltic) must equal the
    states obtained by filtering — that is the correspondence contract."""
    rng = np.random.default_rng(5)
    x = rng.standard_normal(128)
    a = _stable_lpc()
    order = a.size - 1

    e, zf_a = analysis_filter(x, a)
    zi_a = analysis_state_from_history(x, a)
    np.testing.assert_allclose(zi_a, zf_a, atol=1e-14)

    y, zf_s = synthesis_filter(e, a)
    zi_s = synthesis_state_from_history(y, a)
    np.testing.assert_allclose(zi_s, zf_s, atol=1e-14)

    # and the history-derived state reproduces the continuation exactly
    x2 = rng.standard_normal(64)
    e2a, _ = analysis_filter(x2, a, zi=zf_a)
    e2b, _ = analysis_filter(x2, a, zi=zi_a)
    np.testing.assert_allclose(e2a, e2b, atol=1e-14)


def test_wrong_initial_state_breaks_reconstruction_not_residual(ar_signal):
    """Small residual is NOT evidence of lossless reconstruction: with a
    wrong synthesis state the residual stays identical (and small) while
    the reconstruction error is large."""
    from app.lpc.autocorr import apply_window, autocorrelation
    from app.lpc.levinson import levinson_durbin

    x = ar_signal["samples"]
    order = 10
    # coefficients adapted to the (predictable AR) signal: small residual
    a = levinson_durbin(
        autocorrelation(apply_window(x[:256], "hann"), order), order
    ).lpc

    _, zf_a = analysis_filter(x[:256], a)
    e2, _ = analysis_filter(x[256:512], a, zi=zf_a)
    x2 = x[256:512]
    assert np.sum(e2**2) < np.sum(x2**2)  # residual is smaller than the signal

    # corresponding synthesis state -> exact reconstruction
    y_good, _ = synthesis_filter(
        e2, a, zi=synthesis_state_from_history(x[:256], a)
    )
    np.testing.assert_allclose(y_good, x2, atol=1e-10)

    # wrong (zero) state -> large reconstruction error, SAME small residual
    y_bad, _ = synthesis_filter(e2, a, zi=np.zeros(order))
    assert np.max(np.abs(y_bad - x2)) > 1e-3


def test_rejects_non_monic_lpc():
    with pytest.raises(ValueError, match="monic"):
        analysis_filter(np.zeros(8), np.array([2.0, 0.5]))
    with pytest.raises(ValueError, match="monic"):
        synthesis_filter(np.zeros(8), np.array([0.0, 0.5]))
