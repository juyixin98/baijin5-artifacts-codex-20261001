"""Deterministic synthetic fixtures shared by tests and the verify script.

All fixtures are generated locally from closed-form formulas or seeded
generators — no external data, no network, no production accounts.
"""
from __future__ import annotations

import numpy as np

DEFAULT_SAMPLE_RATE = 16_000


def tone(freq_hz: float = 440.0, seconds: float = 1.0, sample_rate: int = DEFAULT_SAMPLE_RATE,
         amplitude: float = 0.8) -> tuple[int, np.ndarray]:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    return sample_rate, amplitude * np.sin(2 * np.pi * freq_hz * t)


def impulse_train(period_samples: int = 160, seconds: float = 1.0,
                  sample_rate: int = DEFAULT_SAMPLE_RATE, amplitude: float = 1.0) -> tuple[int, np.ndarray]:
    n = int(seconds * sample_rate)
    x = np.zeros(n)
    x[::period_samples] = amplitude
    return sample_rate, x


def silence(seconds: float = 1.0, sample_rate: int = DEFAULT_SAMPLE_RATE) -> tuple[int, np.ndarray]:
    return sample_rate, np.zeros(int(seconds * sample_rate))


def noise(seconds: float = 1.0, sample_rate: int = DEFAULT_SAMPLE_RATE, seed: int = 20260927,
          amplitude: float = 0.5) -> tuple[int, np.ndarray]:
    rng = np.random.default_rng(seed)
    return sample_rate, amplitude * rng.standard_normal(int(seconds * sample_rate))


FIXTURES = {
    "tone_440hz": tone,
    "impulse_train_100hz": impulse_train,
    "silence": silence,
    "noise_seeded": noise,
}


def get_fixture(name: str) -> tuple[int, np.ndarray]:
    """Return (sample_rate, float64 mono samples) for a named fixture."""
    if name not in FIXTURES:
        raise KeyError(f"unknown fixture {name!r}; available: {sorted(FIXTURES)}")
    sr, x = FIXTURES[name]()
    return sr, np.asarray(x, dtype=np.float64)
