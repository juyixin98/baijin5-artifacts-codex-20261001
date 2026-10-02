"""Exact tiled execution for rasters too large to transform at once.

Strategy
--------
A nearest source can lie arbitrarily far outside its pixel's tile, so a
naive fixed-halo scheme silently drops cross-tile winners. Instead each
tile is processed in three steps:

1. **Local upper bound.** Run the kernel on the tile using only sources
   inside the tile. The resulting per-pixel distances ``u(p)`` are upper
   bounds on the true distances ``d(p)``, so ``U = max u(p)`` bounds the
   nearest-source distance of *every* pixel in the tile.
2. **Exact halo.** Any source that can win for some tile pixel ``p`` must
   lie within physical distance ``d(p) <= U`` of ``p``; hence all
   candidate sources live inside the tile bounding box expanded by
   ``ceil(U / dy) + 1`` rows and ``ceil(U / dx) + 1`` columns. The window
   is clipped to the raster (sources cannot exist outside it).
3. **Recompute on the window.** Run the kernel on the expanded window and
   crop the tile back out. Because every source within distance ``U`` of
   the tile is present, distances are exact and the full tie set is
   visible, so the deterministic tie-break agrees with the untiled run.

If a tile contains no source at all, step 1 yields no finite bound; the
bound is then derived from the tile corners: for any pixel ``p`` in the
tile and its nearest corner ``k``,

    d(p) <= dist(p, k) + d(k) <= tile_diagonal + max_corner d(k)

where each ``d(k)`` is found by an exact O(S) scan over the global source
list. If the whole raster has no source the caller short-circuits before
tiling.

Tie-break consistency: a window is a contiguous axis-aligned subgrid, so
local (row, col) lexicographic order equals global order, so the kernel's
``(distance, column, row)`` contract is preserved when labels are
translated back to global flat indices.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .kernel import INF, NO_SOURCE, edt2d


@dataclass(frozen=True)
class TileReport:
    """Provenance for one tile: where it ran and how far the halo reached."""

    row0: int
    col0: int
    row1: int
    col1: int
    window: tuple[int, int, int, int]
    upper_bound: float


def _iter_tiles(height: int, width: int, tile_size: int):
    for r0 in range(0, height, tile_size):
        for c0 in range(0, width, tile_size):
            yield r0, min(r0 + tile_size, height), c0, min(c0 + tile_size, width)


def _corner_bound(
    sources_rc: np.ndarray,
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    dy: float,
    dx: float,
) -> float:
    """Upper bound on nearest-source distance for a source-less tile."""
    corners = np.array(
        [[r0, c0], [r0, c1 - 1], [r1 - 1, c0], [r1 - 1, c1 - 1]], dtype=np.float64
    )
    d_rows = (sources_rc[:, 0][None, :] - corners[:, 0][:, None]) * dy
    d_cols = (sources_rc[:, 1][None, :] - corners[:, 1][:, None]) * dx
    corner_dist = np.sqrt(d_rows**2 + d_cols**2).min(axis=1)
    diagonal = math.hypot((r1 - r0 - 1) * dy, (c1 - c0 - 1) * dx)
    return float(corner_dist.max() + diagonal)


def edt2d_tiled(
    mask: np.ndarray,
    spacing: tuple[float, float] = (1.0, 1.0),
    tile_size: int = 512,
):
    """Exact EDT computed tile by tile.

    Returns ``(dist, labels, reports)`` where ``dist``/``labels`` match
    :func:`edt_service.kernel.edt2d` exactly and ``reports`` is a list of
    :class:`TileReport` for observability.
    """
    if mask.ndim != 2:
        raise ValueError("mask must be 2-D")
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    dy, dx = spacing
    height, width = mask.shape

    dist = np.full((height, width), INF, dtype=np.float64)
    labels = np.full((height, width), NO_SOURCE, dtype=np.int64)
    reports: list[TileReport] = []

    sources_rc = np.argwhere(mask)
    if sources_rc.shape[0] == 0:
        return dist, labels, reports

    for r0, r1, c0, c1 in _iter_tiles(height, width, tile_size):
        sub = mask[r0:r1, c0:c1]

        # Step 1: per-pixel upper bound from tile-local sources.
        local_dist, _ = edt2d(sub, spacing)
        finite = np.isfinite(local_dist)
        if finite.any():
            upper = float(local_dist[finite].max())
        else:
            # Step 1b: no local source — bound via tile corners.
            upper = _corner_bound(sources_rc, r0, r1, c0, c1, dy, dx)

        # Step 2: halo large enough to contain every possible winner.
        grow_r = int(math.ceil(upper / dy)) + 1
        grow_c = int(math.ceil(upper / dx)) + 1
        wr0 = max(0, r0 - grow_r)
        wr1 = min(height, r1 + grow_r)
        wc0 = max(0, c0 - grow_c)
        wc1 = min(width, c1 + grow_c)

        # Step 3: exact recompute on the window, crop, relabel globally.
        win_dist, win_labels = edt2d(mask[wr0:wr1, wc0:wc1], spacing)
        win_w = wc1 - wc0
        tile_dist = win_dist[r0 - wr0 : r1 - wr0, c0 - wc0 : c1 - wc0]
        tile_lab = win_labels[r0 - wr0 : r1 - wr0, c0 - wc0 : c1 - wc0]

        valid = tile_lab >= 0
        safe_lab = np.where(valid, tile_lab, 0)
        global_rows = safe_lab // win_w + wr0
        global_cols = safe_lab % win_w + wc0
        dist[r0:r1, c0:c1] = tile_dist
        labels[r0:r1, c0:c1] = np.where(
            valid, global_rows * width + global_cols, NO_SOURCE
        )
        reports.append(
            TileReport(
                row0=r0,
                col0=c0,
                row1=r1,
                col1=c1,
                window=(wr0, wr1, wc0, wc1),
                upper_bound=upper,
            )
        )

    return dist, labels, reports
