"""Biased autocorrelation of a (windowed) frame.

r[k] = sum_{n=k}^{N-1} x[n] * x[n-k],  k = 0..order

This is the autocorrelation method of LPC: the frame is treated as
zero outside its support, which makes the normal-equation matrix
Toeplitz and guarantees a stable synthesis filter whenever the
recursion stays well-conditioned.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import correlate


def autocorrelation(samples: np.ndarray, order: int) -> np.ndarray:
    x = np.asarray(samples, dtype=float)
    if x.size == 0:
        raise ValueError("autocorrelation needs a non-empty frame")
    if order < 0 or order >= x.size:
        raise ValueError(f"order {order} out of range for frame of {x.size} samples")
    full = correlate(x, x, mode="full")
    mid = x.size - 1  # index of lag 0
    return full[mid : mid + order + 1]
