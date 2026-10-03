"""Pre-emphasis: y[n] = x[n] - coef * x[n-1], with the fixed boundary x[-1] = 0
(so y[0] = x[0] exactly — the first sample is *not* attenuated)."""

from __future__ import annotations

import numpy as np


def preemphasis(samples: np.ndarray, coef: float) -> np.ndarray:
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 1 or x.size == 0:
        raise ValueError("preemphasis expects a non-empty 1-D array")
    y = x.copy()
    if x.size > 1:
        y[1:] = x[1:] - coef * x[:-1]
    # y[0] stays x[0]: the implicit previous sample is 0 by spec.
    return y
