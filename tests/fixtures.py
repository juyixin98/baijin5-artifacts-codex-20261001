"""Synthetic fixtures for the FIR estimation tests.

All reference signals are generated with plain NumPy (`np.convolve`,
seeded RNGs) — deliberately *not* with the application code under test —
so expected values are independent references.
"""

from __future__ import annotations

import numpy as np

# A known, fixed channel used across tests. Dominant tap at index 0 so the
# cross-correlation delay estimator has an unambiguous peak.
KNOWN_FIR = np.array([1.0, -0.55, 0.3, 0.15, -0.08, 0.04])


def white_excitation(n: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


def narrowband_excitation(n: int, frequency: float = 0.05, n_tones: int = 1) -> np.ndarray:
    """Sum of incommensurate sinusoids: spans only 2*n_tones dimensions."""
    t = np.arange(n)
    signal = np.zeros(n)
    for k in range(n_tones):
        signal += np.sin(2.0 * np.pi * frequency * (k + 1) * t + 0.3 * k)
    return signal


def make_response(
    excitation: np.ndarray,
    channel: np.ndarray,
    *,
    delay: int = 0,
    noise_std: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """y[n] = (channel * x)[n - delay] + noise, truncated to len(x)."""
    clean = np.convolve(excitation, channel)[: excitation.size]
    if delay > 0:
        clean = np.concatenate([np.zeros(delay), clean[:-delay]])
    if noise_std > 0.0:
        rng = np.random.default_rng(seed)
        clean = clean + noise_std * rng.standard_normal(clean.size)
    return clean
