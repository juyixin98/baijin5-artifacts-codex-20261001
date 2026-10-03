"""Log compression and DCT — the final MFCC stage.

- mel energies are floored at config.log_floor *before* the logarithm, so
  digital silence yields a finite, fully determined constant matrix.
- natural logarithm (fixed spec).
- DCT-II with orthonormal scaling (scipy.fft.dct norm="ortho"), first
  n_mfcc coefficients kept.
"""

from __future__ import annotations

import numpy as np
from scipy.fft import dct


def log_mel_spectrum(mel_energies: np.ndarray, log_floor: float) -> np.ndarray:
    energies = np.asarray(mel_energies, dtype=np.float64)
    if np.any(energies < 0.0):
        raise ValueError("mel energies must be non-negative")
    return np.log(np.maximum(energies, log_floor))


def mfcc_from_log_mel(log_mel: np.ndarray, n_mfcc: int) -> np.ndarray:
    log_mel = np.asarray(log_mel, dtype=np.float64)
    if log_mel.ndim != 2:
        raise ValueError("log_mel must be (n_frames, n_mels)")
    if not (1 <= n_mfcc <= log_mel.shape[1]):
        raise ValueError(f"n_mfcc must be in [1, {log_mel.shape[1]}]")
    coeffs = dct(log_mel, type=2, axis=1, norm="ortho")
    return coeffs[:, :n_mfcc]
