"""Delta and delta-delta computation.

Boundary rule (fixed, explicit): the feature sequence is extended by
*replicating* the first and last frame ``width`` times before applying the
regression window.  With width N:

    delta[t] = sum_{n=1..N} n * (c[t+n] - c[t-n]) / (2 * sum_{n=1..N} n^2)

where indices outside [0, T-1] clamp to the boundary frame.  This is the
standard HTK edge behaviour and makes delta well-defined for every frame,
including the first and the last.

An empty input (0 frames) yields an empty output of shape (0, n_coeffs).
"""

from __future__ import annotations

import numpy as np

from .errors import InputContractError


def compute_delta(features: np.ndarray, width: int) -> np.ndarray:
    """Delta features along axis 0 with edge-replication extension."""
    if width < 1:
        raise InputContractError(f"delta width must be >= 1, got {width}")
    feat = np.asarray(features, dtype=np.float64)
    if feat.ndim != 2:
        raise InputContractError(
            f"delta expects a 2-D (n_frames, n_coeffs) matrix, got shape {feat.shape}"
        )
    n_frames, n_coeffs = feat.shape
    if n_frames == 0:
        return np.empty((0, n_coeffs), dtype=np.float64)
    denom = 2.0 * sum(n * n for n in range(1, width + 1))
    padded = np.pad(feat, ((width, width), (0, 0)), mode="edge")
    out = np.zeros_like(feat)
    for n in range(1, width + 1):
        out += n * (
            padded[width + n : width + n + n_frames]
            - padded[width - n : width - n + n_frames]
        )
    return out / denom


def compute_delta_delta(features: np.ndarray, width: int) -> np.ndarray:
    """Second-order difference: delta of delta, same boundary rule."""
    return compute_delta(compute_delta(features, width), width)
