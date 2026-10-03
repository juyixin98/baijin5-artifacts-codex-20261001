"""Signal-quality tests: frequency preservation, seam continuity, distortion.

All expected values come from the fixture parameters (analytic) and the
independent measurements in wsola_backend.metrics — not from the core.
"""
from __future__ import annotations

import numpy as np
import pytest

from wsola_backend import fixtures, metrics
from wsola_backend.config import DEFAULT_CONFIG
from wsola_backend.wsola import wsola_stretch

SR = fixtures.DEFAULT_SAMPLE_RATE
TONE_FREQ = 440.0
QUALITY_RATES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
EXTREME_RATES = [0.25, 4.0]


@pytest.mark.parametrize("rate", QUALITY_RATES)
def test_tone_dominant_frequency_preserved(rate):
    x = fixtures.tone(TONE_FREQ, duration_s=1.0, sample_rate=SR)
    y = wsola_stretch(x, rate=rate).samples
    assert y.shape[0] == round(x.shape[0] / rate)
    measured = metrics.dominant_frequency(y, SR)
    assert abs(measured - TONE_FREQ) < 3.0  # Hz


@pytest.mark.parametrize("rate", QUALITY_RATES)
def test_tone_distortion_snr_above_threshold(rate):
    x = fixtures.tone(TONE_FREQ, duration_s=1.0, sample_rate=SR)
    y = wsola_stretch(x, rate=rate).samples
    snr_db = metrics.sinusoid_fit_snr_db(y, TONE_FREQ, SR)
    assert snr_db > 20.0  # dB against an independently fitted sinusoid


@pytest.mark.parametrize("rate", QUALITY_RATES)
def test_tone_seam_continuity(rate):
    # Max slope of a 0.5-amp 440 Hz tone at 16 kHz is ~0.086/sample; seams
    # must stay well below a click-level jump.
    x = fixtures.tone(TONE_FREQ, duration_s=1.0, sample_rate=SR)
    result = wsola_stretch(x, rate=rate)
    seams = [s.output_position for s in result.segments[1:]]
    jump = metrics.max_seam_jump(result.samples, seams)
    assert jump < 0.2


@pytest.mark.parametrize("rate", [0.5, 2.0])
def test_impulse_train_period_preserved_count_scales(rate):
    # Time-stretch preserves pitch: the output impulse PERIOD stays at the
    # input period; the impulse COUNT scales with the output duration.
    period = 160  # samples -> 100 Hz at 16 kHz
    n = 16_000
    x = fixtures.impulse_train(period, n_samples=n)
    y = wsola_stretch(x, rate=rate).samples
    spacings = metrics.impulse_spacings(y)
    assert spacings.size > 0
    assert abs(np.median(spacings) - period) <= 2.0
    expected_count = round(n / rate) / period
    assert abs(spacings.size + 1 - expected_count) <= 0.15 * expected_count


def test_silence_stays_silent_at_exact_length():
    x = fixtures.silence(8000)
    result = wsola_stretch(x, rate=1.7)
    assert result.samples.shape[0] == round(8000 / 1.7)
    assert np.all(result.samples == 0.0)


@pytest.mark.parametrize("rate", EXTREME_RATES)
def test_extreme_rates_stay_finite_and_exact_length(rate):
    # Accepted-with-warning range: length contract still holds, output must
    # stay finite, but NO quality guarantee is asserted here (documented).
    x = fixtures.tone(TONE_FREQ, duration_s=1.0, sample_rate=SR)
    result = wsola_stretch(x, rate=rate)
    assert result.samples.shape[0] == round(x.shape[0] / rate)
    assert np.all(np.isfinite(result.samples))


def test_identity_rate_reconstructs_tone_almost_exactly():
    x = fixtures.tone(TONE_FREQ, duration_s=0.5, sample_rate=SR)
    y = wsola_stretch(x, rate=1.0).samples
    # Interior (away from window ramps at the edges) must match closely.
    interior = slice(DEFAULT_CONFIG.window_length, -DEFAULT_CONFIG.window_length)
    assert np.max(np.abs(y[interior] - x[interior])) < 1e-6
