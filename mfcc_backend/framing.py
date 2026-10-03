"""Pre-emphasis, framing and windowing.

Fixed rules (see config module docstring):

- pre-emphasis: y[n] = x[n] - coef * x[n-1]; the very first sample of a stream
  uses x[-1] = 0, i.e. y[0] = x[0].  Streaming callers pass the last raw
  sample of the previous chunk as ``initial`` so chunk boundaries are
  invisible.
- framing: frame t starts at t * hop_length and covers frame_length samples.
  Only *complete* frames are emitted; a trailing partial frame is discarded.
  n_frames = 0 when n_samples < frame_length.
- window: symmetric Hamming, np.hamming(frame_length).
"""

from __future__ import annotations

import numpy as np

from .errors import InputContractError


def validate_samples(samples: np.ndarray, *, max_samples: int | None = None) -> np.ndarray:
    """Enforce the sample contract; return a contiguous float64 array."""
    x = np.asarray(samples, dtype=np.float64).ravel()
    if x.size == 0:
        raise InputContractError("empty sample array: at least one sample is required")
    if not np.all(np.isfinite(x)):
        n_bad = int(np.count_nonzero(~np.isfinite(x)))
        raise InputContractError(
            f"samples contain {n_bad} non-finite value(s) (NaN or inf)",
            details={"non_finite": n_bad},
        )
    if max_samples is not None and x.size > max_samples:
        raise InputContractError(
            f"too many samples: {x.size} > max {max_samples}",
            details={"n_samples": int(x.size), "max_samples": max_samples},
        )
    return np.ascontiguousarray(x)


def preemphasis(samples: np.ndarray, coef: float, *, initial: float = 0.0) -> np.ndarray:
    """y[0] = x[0] - coef*initial; y[n] = x[n] - coef*x[n-1]."""
    x = np.asarray(samples, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    y = np.empty_like(x)
    y[0] = x[0] - coef * initial
    if x.size > 1:
        y[1:] = x[1:] - coef * x[:-1]
    return y


def frame_count(n_samples: int, frame_length: int, hop_length: int) -> int:
    """Number of complete frames; 0 when the signal is shorter than a frame."""
    if n_samples < frame_length:
        return 0
    return 1 + (n_samples - frame_length) // hop_length


def frame_signal(samples: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    """Slice into (n_frames, frame_length); trailing partial frame dropped."""
    x = np.asarray(samples, dtype=np.float64)
    n = frame_count(x.size, frame_length, hop_length)
    if n == 0:
        return np.empty((0, frame_length), dtype=np.float64)
    idx = (
        np.arange(frame_length)[None, :]
        + hop_length * np.arange(n)[:, None]
    )
    return x[idx]


def hamming_window(frame_length: int) -> np.ndarray:
    """Symmetric Hamming window (the fixed window of this pipeline)."""
    return np.hamming(frame_length)
