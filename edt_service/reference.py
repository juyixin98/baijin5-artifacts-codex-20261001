"""Independent brute-force oracle.

Computes the exact same contract as :mod:`edt_service.kernel` by
exhaustively enumerating every source for every pixel — O(pixels x
sources). Deliberately shares *no code* with the kernel so tests compare
two independent implementations of the specification:

- distance: exact Euclidean with anisotropic spacing;
- label: nearest source under the tie-break key
  ``(distance, source_column, source_row)``;
- no source anywhere: distance ``+inf``, label ``-1``.

Only suitable for small rasters (tests and debugging).
"""

from __future__ import annotations

import numpy as np

from .kernel import INF, NO_SOURCE


def brute_force_edt(mask: np.ndarray, spacing: tuple[float, float] = (1.0, 1.0)):
    """Exhaustive per-pixel nearest-source search.

    Returns ``(dist, labels)`` with the same shapes and semantics as
    :func:`edt_service.kernel.edt2d`.
    """
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    dy, dx = spacing
    height, width = mask.shape

    dist = np.full((height, width), INF, dtype=np.float64)
    labels = np.full((height, width), NO_SOURCE, dtype=np.int64)

    sources = np.argwhere(mask)
    if sources.shape[0] == 0:
        return dist, labels

    src_r = sources[:, 0].astype(np.float64)
    src_c = sources[:, 1].astype(np.float64)
    src_rc = sources[:, 0]
    src_cc = sources[:, 1]

    for r in range(height):
        drow2 = ((r - src_r) * dy) ** 2
        for c in range(width):
            d2 = drow2 + ((c - src_c) * dx) ** 2
            best = np.min(d2)
            # Deterministic tie-break: among all sources attaining the
            # minimum distance pick smallest column, then smallest row.
            tied = np.flatnonzero(d2 == best)
            tie_cols = src_cc[tied]
            min_col = tie_cols.min()
            rows_at_min_col = src_rc[tied[tie_cols == min_col]]
            src_row = rows_at_min_col.min()
            dist[r, c] = np.sqrt(best)
            labels[r, c] = src_row * width + min_col
    return dist, labels
