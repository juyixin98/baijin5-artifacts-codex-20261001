"""Numeric kernel: exact Euclidean distance transform of a binary raster.

Algorithm
---------
Separable lower-envelope (Felzenszwalb & Huttenlocher) transform. The
squared Euclidean distance decomposes along axes::

    D2(r, c) = min over sources (sr, sc) of
               ((r - sr) * dy)^2 + ((c - sc) * dx)^2

so an exact 1-D transform (lower envelope of parabolas, linear time) is
applied along rows for every column, then along columns for every row.
This is an *exact* Euclidean transform — no chamfer/Manhattan
approximation is involved anywhere.

Pixel spacing is anisotropic: ``spacing=(dy, dx)`` with independent
positive row/column pitches.

Nearest-source identity
-----------------------
Each 1-D pass also propagates the index of the winning parabola, so the
2-D result carries the identity of *a* nearest source for every pixel.

Tie-break contract (deterministic, documented, mirrored by
``reference.brute_force_edt``): among sources at equal distance the
winner is chosen by the key ``(distance, source_column, source_row)`` —
smallest distance, then smallest column index, then smallest row index.
The row pass naturally yields "smallest row wins" inside one column and
the column pass "smallest column wins" across columns, which is exactly
this key. Ties are broken on the *values* compared during envelope
construction; earlier (lower-index) parabolas win exact ties.

Degenerate rasters
------------------
- No source pixel: every distance is ``+inf`` and every label is ``-1``.
- All pixels are sources: every distance is ``0`` and every label is the
  pixel's own flat index. Both fall out of the kernel naturally and are
  additionally given explicit fast paths in ``service.compute_edt``.
"""

from __future__ import annotations

import numpy as np

INF = np.inf
NO_SOURCE = -1


def dt1d(f: np.ndarray, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Exact 1-D squared-distance transform with argmin propagation.

    Parameters
    ----------
    f:
        Sampled values; may contain ``+inf`` (parabolas that never win).
    spacing:
        Physical pitch between consecutive samples (positive, finite).

    Returns
    -------
    (d, idx):
        ``d[p]  = min_q f[q] + ((p - q) * spacing) ** 2``
        ``idx[p] = argmin q`` — on exact ties the *smallest* ``q`` wins.
    """
    n = f.shape[0]
    d = np.full(n, INF, dtype=np.float64)
    idx = np.full(n, NO_SOURCE, dtype=np.int64)
    if n == 0:
        return d, idx

    finite = np.flatnonzero(np.isfinite(f))
    if finite.size == 0:
        return d, idx

    s2 = spacing * spacing
    v = np.zeros(n, dtype=np.int64)  # parabola positions in the envelope
    z = np.zeros(n + 1, dtype=np.float64)  # interval boundaries

    k = 0
    v[0] = finite[0]
    z[0] = -INF
    z[1] = INF

    for q in finite[1:]:
        fq = f[q]
        while True:
            p = v[k]
            # Intersection of parabolas centred at p and q (q > p):
            #   s = ((fq + s2 q^2) - (fp + s2 p^2)) / (2 s2 (q - p))
            s = ((fq + s2 * q * q) - (f[p] + s2 * p * p)) / (2.0 * s2 * (q - p))
            # Strict '<': on an exact boundary tie the *earlier* parabola
            # keeps the tie point, so the smaller index wins equal values.
            if s < z[k]:
                k -= 1
                if k < 0:
                    k = 0
                    v[0] = q
                    z[0] = -INF
                    z[1] = INF
                    break
            else:
                k += 1
                v[k] = q
                z[k] = s
                z[k + 1] = INF
                break

    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        p = v[k]
        d[q] = f[p] + s2 * (q - p) * (q - p)
        idx[q] = p
    return d, idx


def edt2d(mask: np.ndarray, spacing: tuple[float, float] = (1.0, 1.0)):
    """Exact 2-D Euclidean distance transform of a binary raster.

    Parameters
    ----------
    mask:
        2-D boolean array; ``True`` marks a source pixel.
    spacing:
        ``(dy, dx)`` physical pitch of rows and columns.

    Returns
    -------
    (dist, labels):
        ``dist``   float64 array of Euclidean distances (``+inf`` when the
                   raster has no source).
        ``labels`` int64 array of flat indices ``row * width + col`` of a
                   nearest source, or ``-1`` when there is no source.
                   Ties follow the module-level tie-break contract.
    """
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    dy, dx = spacing
    height, width = mask.shape

    f = np.where(mask, 0.0, INF)

    # Pass 1: along rows, independently per column. Records, for every
    # cell, the row of the winning source *within its own column*.
    col_dist = np.empty((height, width), dtype=np.float64)
    col_row = np.empty((height, width), dtype=np.int64)
    for c in range(width):
        col_dist[:, c], col_row[:, c] = dt1d(f[:, c], dy)

    # Pass 2: along columns, independently per row. The winner identifies
    # the source column; the source row comes from pass 1.
    dist2 = np.empty((height, width), dtype=np.float64)
    labels = np.empty((height, width), dtype=np.int64)
    for r in range(height):
        dist2[r, :], best_col = dt1d(col_dist[r, :], dx)
        src_row = np.where(best_col >= 0, col_row[r, np.maximum(best_col, 0)], 0)
        labels[r, :] = np.where(
            best_col >= 0, src_row * width + np.maximum(best_col, 0), NO_SOURCE
        )

    with np.errstate(invalid="ignore"):
        dist = np.sqrt(dist2)
    return dist, labels
