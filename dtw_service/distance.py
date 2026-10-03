"""Fixed local distance metric.

The metric is part of the behavior contract: absolute difference between
scalar feature values (cityblock on 1-D frames). It is deliberately *not*
configurable — comparability of normalized costs across requests depends on
everyone using the same local metric, step pattern, and window semantics.
"""

from __future__ import annotations

import numpy as np
from scipy.spatial.distance import cdist

METRIC_NAME = "absolute"


def local_distance(x: float, y: float) -> float:
    """Local distance between two scalar feature values."""
    return abs(float(x) - float(y))


def distance_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Full ``len(a) x len(b)`` local distance matrix (dense path only)."""
    a = np.asarray(a, dtype=float).reshape(-1, 1)
    b = np.asarray(b, dtype=float).reshape(-1, 1)
    return cdist(a, b, metric="cityblock")


def band_max_distance(a: np.ndarray, b: np.ndarray, radius: int) -> float:
    """Maximum local distance over the Sakoe-Chiba band.

    Used by the service to detect the degenerate case where *every* cell in
    the band has distance zero, making all legal paths equally optimal.
    """
    worst = 0.0
    m = len(b)
    for i, x in enumerate(a):
        j_lo = max(0, i - radius)
        j_hi = min(m - 1, i + radius)
        if j_lo > j_hi:
            continue
        seg = np.abs(b[j_lo : j_hi + 1] - x)
        if seg.size:
            worst = max(worst, float(seg.max()))
    return worst
