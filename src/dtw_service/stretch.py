"""Local stretch-rate estimation from a warping path.

The local stretch rate is the slope of the warping path, dj/di: how many
reference frames advance per query frame, estimated over a centered window of
path steps. A rate of 2.0 means the reference is locally stretched 2x relative
to the query (two reference frames per query frame); 0.5 means compressed 2x.

A window in which the query index never advances (pure vertical segment)
yields ``numpy.inf`` -- the rate is genuinely unbounded there, and callers
must decide how to present it (the API maps it to null).
"""

from __future__ import annotations

import numpy as np

DEFAULT_WINDOW_STEPS = 5


def local_stretch_rates(
    path: list[tuple[int, int]], window_steps: int = DEFAULT_WINDOW_STEPS
) -> np.ndarray:
    """Per-path-step stretch rate dj/di over a centered +/-window_steps window."""
    if window_steps < 1:
        raise ValueError("window_steps must be >= 1")
    if not path:
        raise ValueError("path must be non-empty")

    idx_i = np.array([p[0] for p in path], dtype=np.float64)
    idx_j = np.array([p[1] for p in path], dtype=np.float64)
    length = len(path)
    rates = np.full(length, np.inf)
    for k in range(length):
        lo = max(0, k - window_steps)
        hi = min(length - 1, k + window_steps)
        di = idx_i[hi] - idx_i[lo]
        dj = idx_j[hi] - idx_j[lo]
        if di > 0:
            rates[k] = dj / di
    return rates


def mean_stretch_rate(path: list[tuple[int, int]]) -> float:
    """Global stretch: total reference advance / total query advance."""
    if len(path) < 2:
        return 1.0
    di = path[-1][0] - path[0][0]
    dj = path[-1][1] - path[0][1]
    if di == 0:
        return float("inf")
    return dj / di
