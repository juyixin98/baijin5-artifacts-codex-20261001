"""Temporal derivatives with an explicit boundary rule.

delta[t]   = sum_{n=1..N} n * (c[t+n] - c[t-n]) / (2 * sum_{n=1..N} n^2)

Boundary: out-of-range frame indices are clamped to the nearest valid frame
(edge replication), i.e. c[-1] = c[0] and c[T] = c[T-1]. The same rule is
applied for the delta of the delta (delta2), and — crucially — at the end
of a finished stream, so batch and streaming outputs agree bit-for-bit.
"""

from __future__ import annotations

import numpy as np


def compute_delta(features: np.ndarray, width: int) -> np.ndarray:
    feats = np.asarray(features, dtype=np.float64)
    if feats.ndim != 2:
        raise ValueError("features must be (n_frames, n_coeffs)")
    if width < 1:
        raise ValueError("width must be >= 1")
    n_frames = feats.shape[0]
    if n_frames == 0:
        return np.empty_like(feats)

    padded = np.pad(feats, ((width, width), (0, 0)), mode="edge")
    denom = 2.0 * sum(n * n for n in range(1, width + 1))
    out = np.zeros_like(feats)
    for n in range(1, width + 1):
        out += n * (padded[width + n : width + n + n_frames]
                    - padded[width - n : width - n + n_frames])
    return out / denom
