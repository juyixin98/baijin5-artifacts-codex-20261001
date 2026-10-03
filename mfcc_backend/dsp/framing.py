"""Framing: fixed symmetric Hamming window, frames anchored at sample 0,
incomplete trailing frame dropped (never zero-padded).

Frame count: n_frames = 1 + (n_samples - frame_length) // hop_length.
A signal shorter than one frame is an explicit error, not an empty result.
"""

from __future__ import annotations

import numpy as np

from ..errors import InsufficientSignalError


def hamming_window(length: int) -> np.ndarray:
    """Symmetric Hamming: w[n] = 0.54 - 0.46*cos(2*pi*n/(N-1))."""
    if length < 2:
        raise ValueError("hamming window needs length >= 2")
    n = np.arange(length, dtype=np.float64)
    return 0.54 - 0.46 * np.cos(2.0 * np.pi * n / (length - 1))


def frame_signal(
    samples: np.ndarray, frame_length: int, hop_length: int
) -> np.ndarray:
    """Return the windowed frame matrix of shape (n_frames, frame_length)."""
    x = np.asarray(samples, dtype=np.float64)
    n = x.size
    if n < frame_length:
        raise InsufficientSignalError(
            f"signal has {n} sample(s) but one frame needs {frame_length}",
            detail={"n_samples": int(n), "frame_length": int(frame_length)},
        )
    n_frames = 1 + (n - frame_length) // hop_length
    # Samples beyond the last full frame are dropped by construction.
    starts = np.arange(n_frames) * hop_length
    idx = starts[:, None] + np.arange(frame_length)[None, :]
    return x[idx] * hamming_window(frame_length)
