"""Mel filterbank construction (HTK scale, triangular, unnormalized).

The sample rate alone determines the FFT frequency axis:
bin k sits at k * sample_rate / n_fft Hz, k = 0 .. n_fft//2.

A filter whose triangular support contains no FFT bin with non-zero weight
is an *empty-support* filter.  That happens silently when the mel spacing is
finer than the FFT bin spacing (low n_fft, many filters, low sample rate) and
would turn the log step into log(0) guarded only by the floor — so it is
detected and reported as EmptyFilterError instead of being silently floored.
"""

from __future__ import annotations

import numpy as np

from .config import MFCCConfig
from .errors import EmptyFilterError

_HTK_A = 2595.0
_HTK_B = 700.0


def hz_to_mel(freq_hz: np.ndarray | float) -> np.ndarray:
    """HTK mel scale: mel = 2595 * log10(1 + f / 700)."""
    return _HTK_A * np.log10(1.0 + np.asarray(freq_hz, dtype=np.float64) / _HTK_B)


def mel_to_hz(mel: np.ndarray | float) -> np.ndarray:
    """Inverse of hz_to_mel."""
    return _HTK_B * (10.0 ** (np.asarray(mel, dtype=np.float64) / _HTK_A) - 1.0)


def fft_frequencies(config: MFCCConfig) -> np.ndarray:
    """Frequency (Hz) of each rfft bin — the sample-rate-determined axis."""
    return np.arange(config.n_freq_bins, dtype=np.float64) * (
        config.sample_rate / config.n_fft
    )


def mel_filter_points(config: MFCCConfig) -> np.ndarray:
    """n_mels + 2 boundary frequencies (Hz) of the triangular filters."""
    lo = hz_to_mel(config.fmin)
    hi = hz_to_mel(config.resolved_fmax)
    mel_points = np.linspace(lo, hi, config.n_mels + 2)
    return mel_to_hz(mel_points)


def build_mel_filterbank(config: MFCCConfig) -> np.ndarray:
    """Return the (n_mels, n_freq_bins) filterbank matrix.

    Raises EmptyFilterError if any filter has zero total weight.
    """
    config.validate()
    freqs = fft_frequencies(config)
    pts = mel_filter_points(config)
    fb = np.zeros((config.n_mels, config.n_freq_bins), dtype=np.float64)
    for m in range(config.n_mels):
        left, center, right = pts[m], pts[m + 1], pts[m + 2]
        # Guard against degenerate (duplicate) mel points: the triangle would
        # have zero width and divide by zero.
        if not (left < center < right):
            raise EmptyFilterError(
                f"mel filter {m} is degenerate: "
                f"left={left:.3f} center={center:.3f} right={right:.3f} Hz",
                details={"filter": m, "points_hz": [left, center, right]},
            )
        up = (freqs - left) / (center - left)
        down = (right - freqs) / (right - center)
        fb[m] = np.maximum(0.0, np.minimum(up, down))
    empty = np.nonzero(fb.sum(axis=1) == 0.0)[0]
    if empty.size:
        raise EmptyFilterError(
            f"{empty.size} mel filter(s) have empty support on the "
            f"{config.n_fft}-point FFT axis at {config.sample_rate} Hz: "
            f"indices {empty.tolist()}",
            details={
                "empty_filters": empty.tolist(),
                "n_fft": config.n_fft,
                "sample_rate": config.sample_rate,
            },
        )
    return fb
