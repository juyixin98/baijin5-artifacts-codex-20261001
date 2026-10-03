"""Local synthetic fixtures. No external data, no accounts, fully seeded.

These generators are the reusable fixtures for tests AND for the
verification script; expected values in tests are derived analytically
from the generator parameters, not from the WSOLA core under test.
"""
from __future__ import annotations

import numpy as np

DEFAULT_SAMPLE_RATE = 16_000


def tone(
    freq_hz: float,
    duration_s: float,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: float = 0.5,
    phase: float = 0.0,
) -> np.ndarray:
    """Pure sinusoid; dominant frequency and seam bounds are analytic."""
    n = int(round(duration_s * sample_rate))
    t = np.arange(n) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * freq_hz * t + phase)


def impulse_train(
    period_samples: int,
    n_samples: int,
    amplitude: float = 1.0,
    offset: int = 0,
) -> np.ndarray:
    """Unit impulses every period_samples, first one at `offset`."""
    x = np.zeros(n_samples)
    positions = np.arange(offset, n_samples, period_samples)
    x[positions] = amplitude
    return x


def silence(n_samples: int) -> np.ndarray:
    return np.zeros(n_samples)


def noise(n_samples: int, seed: int, amplitude: float = 0.3) -> np.ndarray:
    """Deterministic white noise (seeded; reproducible across runs)."""
    rng = np.random.default_rng(seed)
    return amplitude * rng.standard_normal(n_samples)


def tone_with_silence_gap(
    freq_hz: float,
    tone_samples: int,
    gap_samples: int,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    amplitude: float = 0.5,
) -> np.ndarray:
    """Tone - silence - tone: exercises the degenerate-match rule mid-signal."""
    t = np.arange(tone_samples) / sample_rate
    segment = amplitude * np.sin(2.0 * np.pi * freq_hz * t)
    return np.concatenate([segment, np.zeros(gap_samples), segment])
