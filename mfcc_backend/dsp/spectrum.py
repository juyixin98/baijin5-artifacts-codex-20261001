"""Power spectrum with an explicit, sample-rate-derived frequency axis.

S[k] = |rfft(frame, nfft)|^2 / nfft,  f[k] = k * sample_rate / nfft.
"""

from __future__ import annotations

import numpy as np


def fft_frequencies(sample_rate: int, nfft: int) -> np.ndarray:
    """Frequency of each rfft bin in Hz — the axis the mel bank is built on."""
    return np.arange(nfft // 2 + 1, dtype=np.float64) * (sample_rate / nfft)


def power_spectrum(frames: np.ndarray, nfft: int) -> np.ndarray:
    frames = np.asarray(frames, dtype=np.float64)
    if frames.ndim != 2 or frames.shape[1] > nfft:
        raise ValueError("frames must be (n_frames, <= nfft)")
    spec = np.fft.rfft(frames, n=nfft, axis=1)
    return (spec.real**2 + spec.imag**2) / nfft
