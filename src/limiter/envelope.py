"""Gain-envelope primitives (pure functions, no streaming state).

All functions implement the fixed formulas documented in ``config.py``.
None of them ever clips a sample: the ceiling is structural, because the
final gain satisfies ``g[n] <= r[n]`` for every n by construction.
"""

from __future__ import annotations

import numpy as np


def required_gain(peaks: np.ndarray, threshold: float) -> np.ndarray:
    """r[n] = min(1, threshold / peak[n]); silent samples map to 1."""
    peaks = np.asarray(peaks, dtype=np.float64)
    out = np.ones_like(peaks)
    np.divide(threshold, peaks, out=out, where=peaks > 0.0)
    np.minimum(out, 1.0, out=out)
    return out


def attack_limited_minimum(
    r: np.ndarray, attack_coeff: float, lookahead: int
) -> np.ndarray:
    """Backward exponential ramp: g1[n] = min_{0<=j<=L} r[n+j] * att**(-j).

    Samples beyond the end of ``r`` are treated as r = 1 (no limiting),
    which is exactly what the streaming layer guarantees by always keeping
    ``lookahead`` future samples buffered before emitting.
    """
    r = np.asarray(r, dtype=np.float64)
    n = r.shape[0]
    ext = np.empty(n + lookahead, dtype=np.float64)
    ext[:n] = r
    ext[n:] = 1.0
    g1 = ext[:n].copy()
    factor = 1.0
    for j in range(1, lookahead + 1):
        factor /= attack_coeff
        if not np.isfinite(factor):
            # att**(-j) overflowed: those terms can never be the minimum.
            break
        np.minimum(g1, ext[j : j + n] * factor, out=g1)
    return g1


def release_pass(
    g1: np.ndarray, release_coeff: float, initial: float = 1.0
) -> np.ndarray:
    """Forward release smoothing: g[n] = min(g1[n], g[n-1] * rel).

    ``initial`` carries the gain state across block boundaries so the
    trajectory is independent of how the input was chunked.
    """
    g1 = np.asarray(g1, dtype=np.float64)
    g = np.empty_like(g1)
    prev = float(initial)
    for i in range(g1.shape[0]):
        target = g1[i]
        recovered = prev * release_coeff
        prev = target if target < recovered else recovered
        g[i] = prev
    return g


def gain_trajectory(
    r: np.ndarray,
    attack_coeff: float,
    release_coeff: float,
    lookahead: int,
    initial: float = 1.0,
) -> np.ndarray:
    """Full envelope: attack anticipation followed by release smoothing."""
    return release_pass(
        attack_limited_minimum(r, attack_coeff, lookahead), release_coeff, initial
    )
