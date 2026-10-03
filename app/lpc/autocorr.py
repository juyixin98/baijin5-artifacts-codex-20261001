"""Windowing and autocorrelation estimation.

The method is fixed to the *autocorrelation method* of LPC: the frame is
windowed, then the biased deterministic autocorrelation r[0..order] is
computed. Window shape and order come from the fixed request config.
"""

from __future__ import annotations

import numpy as np

from app.config import SUPPORTED_WINDOWS


def apply_window(x: np.ndarray, window: str) -> np.ndarray:
    """Apply a fixed analysis window to a frame.

    Raises:
        ValueError: for an unsupported window name.
    """
    x = np.asarray(x, dtype=np.float64)
    if window == "hann":
        w = np.hanning(len(x))
    elif window == "hamming":
        w = np.hamming(len(x))
    elif window == "rect":
        w = np.ones(len(x))
    else:
        raise ValueError(
            f"unsupported window {window!r}; supported: {sorted(SUPPORTED_WINDOWS)}"
        )
    return x * w


def autocorrelation(x: np.ndarray, order: int) -> np.ndarray:
    """Biased deterministic autocorrelation r[0..order] of a 1-D frame.

    r[k] = sum_n x[n] * x[n-k]  (no normalization), computed via FFT for
    numerical robustness and rounded back to the real domain.

    Raises:
        ValueError: if order is not positive or not smaller than len(x).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise ValueError("autocorrelation expects a 1-D frame")
    if order < 1 or order >= len(x):
        raise ValueError(
            f"order must satisfy 1 <= order < len(frame); got order={order}, "
            f"len={len(x)}"
        )
    n_fft = 1 << (2 * len(x) - 1).bit_length()
    spectrum = np.fft.rfft(x, n_fft)
    r_full = np.fft.irfft(spectrum * np.conj(spectrum), n_fft)
    r = r_full[: order + 1]
    # Numerical guard: r[0] is an energy and must not be negative.
    if r[0] < 0:
        r[0] = 0.0
    return np.ascontiguousarray(r)
