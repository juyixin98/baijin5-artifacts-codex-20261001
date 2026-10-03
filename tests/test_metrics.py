"""Metric sanity tests against closed-form references."""
import numpy as np
import pytest

from fixtures.synth import get_fixture
from wsola_backend.metrics import (
    dominant_frequency,
    impulse_spacings,
    seam_report,
    sinusoid_residual_snr_db,
)

SR = 16_000


def test_dominant_frequency_on_known_tone():
    _, x = get_fixture("tone_440hz")
    assert dominant_frequency(x, SR) == pytest.approx(440.0, abs=0.5)


def test_sinusoid_fit_scores_tone_high_and_noise_low():
    _, tone = get_fixture("tone_440hz")
    _, noise = get_fixture("noise_seeded")
    assert sinusoid_residual_snr_db(tone, SR, 440.0) > 60.0
    assert sinusoid_residual_snr_db(noise, SR, 440.0) < 15.0


def test_impulse_spacings_exact_on_fixture():
    _, x = get_fixture("impulse_train_100hz")
    spacings = impulse_spacings(x)
    assert spacings.size > 0
    assert np.all(spacings == 160)


def test_seam_report_flags_a_known_discontinuity():
    x = np.zeros(1000)
    x[500] = 1.0  # single jump at a join position
    report = seam_report(x, [500])
    assert report["join_jumps"][500] == pytest.approx(1.0)
    assert report["worst_jump"] == pytest.approx(1.0)
