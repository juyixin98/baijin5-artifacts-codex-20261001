"""Fixed window functions applied before autocorrelation.

The window is part of the algorithm contract: analysis always windows the
frame before computing the autocorrelation, but the residual/reconstruction
filters run on the *raw* samples so that round-trip reconstruction is exact.
"""
from __future__ import annotations

import numpy as np

from app.config import SUPPORTED_WINDOWS


def get_window(name: str, n: int) -> np.ndarray:
    if name == "hann":
        return np.hanning(n)
    if name == "hamming":
        return np.hamming(n)
    if name == "rect":
        return np.ones(n)
    raise ValueError(
        f"unsupported window {name!r}; supported: {', '.join(SUPPORTED_WINDOWS)}"
    )


def apply_window(samples: np.ndarray, name: str) -> np.ndarray:
    samples = np.asarray(samples, dtype=float)
    return samples * get_window(name, samples.size)
