"""Plain helper utilities for tests (not pytest fixtures).

Kept out of conftest.py so tests import these by name without depending on
pytest's conftest module resolution.
"""
from __future__ import annotations

import numpy as np

from aipw.crossfit import FoldScalerStats


def shifted_folds(dataset, k: int) -> np.ndarray:
    """Distribution-shifted, non-random folds: blocks sorted on covariate 0.

    Validation fold f is a contiguous slice of rows ordered by x[:,0], so its
    covariate mean is far from the training mean. Used by leakage-power tests.
    """
    order = np.argsort(dataset.x[:, 0])
    fold_id = np.empty(dataset.n, dtype=np.int64)
    blocks = np.array_split(order, k)
    for f, idx in enumerate(blocks):
        fold_id[idx] = f
    return fold_id


def fake_scaler_stats(fold_id, x, a, arm, k, leaky=False):
    """Per-fold scaler stats as a correct (or deliberately leaky) pipeline would.

    clean (``leaky=False``): stats from train-fold rows of the given arm —
    identical to what ``cross_fit`` stores.
    leaky (``leaky=True``) : stats from ALL rows of the arm, simulating a
    standardizer that saw validation rows at fit time.
    """
    out = []
    for f in range(k):
        rows = (a == arm) if leaky else ((fold_id != f) & (a == arm))
        xx = x[rows]
        scale = xx.std(axis=0)
        scale = np.where(scale > 1e-12, scale, 1.0)
        out.append(FoldScalerStats(fold=f, mean=xx.mean(axis=0), scale=scale))
    return tuple(out)
