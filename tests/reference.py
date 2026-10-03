"""Independent reference convolutions used to check the engine under test.

Crucially, none of these are produced by the partitioned-FFT engine:

* :func:`direct_convolve_py` — pure-Python O(n*m) time-domain convolution.
  Slow; used only for small cases.  Shares no code with NumPy or the engine.
* :func:`direct_convolve_np` — ``numpy.convolve``, NumPy's C direct
  (non-FFT) convolution.
* :func:`fft_convolve_scipy` — ``scipy.signal.fftconvolve``, an independent
  FFT implementation with its own zero-padding and overlap logic.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import fftconvolve


def direct_convolve_py(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    x = [float(v) for v in x]
    h = [float(v) for v in h]
    out = [0.0] * (len(x) + len(h) - 1)
    for i, xv in enumerate(x):
        if xv == 0.0:
            continue
        for j, hv in enumerate(h):
            out[i + j] += xv * hv
    return np.asarray(out, dtype=np.float64)


def direct_convolve_np(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    return np.convolve(np.asarray(x, dtype=np.float64), np.asarray(h, dtype=np.float64))


def fft_convolve_scipy(x: np.ndarray, h: np.ndarray) -> np.ndarray:
    return fftconvolve(
        np.asarray(x, dtype=np.float64), np.asarray(h, dtype=np.float64), mode="full"
    )
