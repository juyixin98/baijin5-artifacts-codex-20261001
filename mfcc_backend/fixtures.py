"""Local synthetic fixtures — no external data, no models, no accounts.

Every signal is deterministic (fixed seeds / closed-form formulas) so tests
and the demo are bit-reproducible.
"""

from __future__ import annotations

import numpy as np


def make_sine(
    freq_hz: float = 440.0,
    duration_s: float = 1.0,
    sample_rate: int = 16000,
    amplitude: float = 0.5,
) -> np.ndarray:
    t = np.arange(int(round(duration_s * sample_rate)), dtype=np.float64) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * freq_hz * t)


def make_white_noise(
    duration_s: float = 1.0,
    sample_rate: int = 16000,
    seed: int = 1234,
    amplitude: float = 0.2,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return amplitude * rng.standard_normal(int(round(duration_s * sample_rate)))


def make_silence(duration_s: float = 1.0, sample_rate: int = 16000) -> np.ndarray:
    return np.zeros(int(round(duration_s * sample_rate)), dtype=np.float64)


def make_short(sample_rate: int = 16000, n_samples: int = 100) -> np.ndarray:
    """Fewer samples than the default 400-sample frame -> must fail loudly."""
    rng = np.random.default_rng(0)
    return 0.1 * rng.standard_normal(n_samples)


def make_one_frame(sample_rate: int = 16000, frame_length: int = 400) -> np.ndarray:
    """Exactly one frame: exercises the T=1 delta boundary (edge replication)."""
    t = np.arange(frame_length, dtype=np.float64) / sample_rate
    return 0.3 * np.sin(2.0 * np.pi * 220.0 * t)


FIXTURES = {
    "sine_440": make_sine,
    "white_noise": make_white_noise,
    "silence": make_silence,
    "too_short": make_short,
    "one_frame": make_one_frame,
}
