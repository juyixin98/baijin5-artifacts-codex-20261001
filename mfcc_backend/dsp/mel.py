"""HTK-style mel filterbank built on the FFT frequency axis.

- mel(f) = 2595 * log10(1 + f / 700)            (HTK scale, fixed)
- n_mels + 2 points equally spaced in mel between fmin and fmax
- triangular filters, no area normalisation (fixed spec)
- a filter whose triangle contains no FFT bin with non-zero weight has
  *empty support*: instead of silently emitting log(floor) constants we
  raise EmptyFilterSupportError naming the offending filter.
"""

from __future__ import annotations

import math

import numpy as np

from ..config import MFCCConfig
from ..errors import EmptyFilterSupportError
from .spectrum import fft_frequencies


def hz_to_mel(freq_hz: float) -> float:
    return 2595.0 * math.log10(1.0 + freq_hz / 700.0)


def mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def build_mel_filterbank(config: MFCCConfig) -> np.ndarray:
    """Return the (n_mels, n_freq_bins) filterbank matrix.

    Raises EmptyFilterSupportError if any filter is all-zero on this
    sample-rate/nfft grid.
    """
    config.validate()
    n_mels = config.n_mels
    n_bins = config.n_freq_bins
    freqs = fft_frequencies(config.sample_rate, config.nfft)

    mel_lo, mel_hi = hz_to_mel(config.fmin_hz), hz_to_mel(config.fmax)
    mel_points = np.linspace(mel_lo, mel_hi, n_mels + 2)
    hz_points = np.array([mel_to_hz(m) for m in mel_points])

    bank = np.zeros((n_mels, n_bins), dtype=np.float64)
    for m in range(n_mels):
        left, center, right = hz_points[m], hz_points[m + 1], hz_points[m + 2]
        if not (left < center < right):
            raise EmptyFilterSupportError(
                f"mel filter {m} has a degenerate triangle "
                f"({left:.3f}, {center:.3f}, {right:.3f}) Hz",
                detail={"filter": m, "triangle_hz": [left, center, right]},
            )
        up = (freqs - left) / (center - left)
        down = (right - freqs) / (right - center)
        bank[m] = np.maximum(0.0, np.minimum(up, down))

    empty = [m for m in range(n_mels) if not np.any(bank[m] > 0.0)]
    if empty:
        bin_hz = config.sample_rate / config.nfft
        raise EmptyFilterSupportError(
            f"mel filter(s) {empty} have no FFT bin inside their support: "
            f"bin spacing is {bin_hz:.3f} Hz (nfft={config.nfft} at "
            f"{config.sample_rate} Hz); increase frame_length_ms or reduce n_mels",
            detail={
                "empty_filters": empty,
                "bin_spacing_hz": bin_hz,
                "nfft": config.nfft,
                "sample_rate": config.sample_rate,
            },
        )
    return bank
