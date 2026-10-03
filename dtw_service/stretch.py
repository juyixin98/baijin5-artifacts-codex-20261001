"""Local stretch-rate derivation from an alignment path.

For each transition ``(i, j) -> (i+di, j+dj)`` the local stretch rate is
``di / dj``: how many samples of sequence A are consumed per sample of
sequence B. Under the fixed step pattern this is one of ``{0.5, 1.0, 2.0}``;
the smoothed series (uniform filter, edge-padded) exposes the local tempo
of sequence A relative to sequence B along the alignment.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter1d


def step_stretches(path: list[tuple[int, int]]) -> list[float]:
    """Per-transition stretch rates ``di / dj`` along the path."""
    rates: list[float] = []
    for (i0, j0), (i1, j1) in zip(path, path[1:]):
        di, dj = i1 - i0, j1 - j0
        if dj <= 0:
            raise ValueError(f"non-monotone path transition {(i0, j0)} -> {(i1, j1)}")
        rates.append(di / dj)
    return rates


def smooth_stretch(rates: list[float], window: int) -> list[float]:
    """Edge-padded moving average of the per-transition stretch rates."""
    if not rates:
        return []
    if window <= 1 or len(rates) == 1:
        return list(rates)
    smoothed = uniform_filter1d(
        np.asarray(rates, dtype=float), size=window, mode="nearest"
    )
    return smoothed.tolist()
