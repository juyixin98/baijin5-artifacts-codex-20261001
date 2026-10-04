"""Tiled execution of the exact EDT for very large rasters.

Why tiling is subtle
--------------------
A naive "compute the EDT independently inside each block" is wrong: the
nearest source of a pixel near a block boundary can live in an adjacent
block. This module uses a *halo (ghost cell)* strategy with a rigorously
sized overlap:

* The EDT is computed by :mod:`app.kernel` on an interior tile expanded with
  a halo of ``ceil(D_max / pitch)`` rows/columns on every side, where
  ``D_max`` is an upper bound on the true nearest-source distance of every
  pixel in the tile.

* Pixels of the interior are at least ``halo * pitch`` away from the padded
  window edge. Any source outside the window is strictly farther than that,
  so if every interior pixel's true distance is below ``D_max``, no source
  outside the window can be its nearest source. Hence the nearest source
  found inside the window equals the global nearest source — cross-block
  sources are never missed.

``D_max`` is obtained cheaply from a **Manhattan-source-distance flood fill**
(``scipy.ndimage.distance_transform_cdt``). For source set S and target p,
the Manhattan source step distance M(p) = min_s (|dy|+|dx|) satisfies

    min(sy, sx) * M(p) <= euclidean(p, S) <= sqrt(sy**2+sx**2) * M(p)

so ``D_max = sqrt(sy**2+sx**2) * M(p)`` is a valid *upper bound* on the
Euclidean distance for every pixel (it is only a bound, never a reported
result; the reported distance always comes from the exact kernel). With no
sources, or on a tile whose M exceeds the halo reach of the whole array, the
window is simply clipped to the full array bounds — i.e. correctness always
wins over memory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .kernel import EDTResult, edt


@dataclass(frozen=True)
class TileWindow:
    """A padded processing window and the interior it must produce."""

    y0: int
    x0: int
    y1: int
    x1: int  # padded window (half-open)
    inner_y0: int
    inner_x0: int
    inner_y1: int
    inner_x1: int  # interior (array coordinates, half-open)


@dataclass(frozen=True)
class TilingReport:
    windows: list[TileWindow]
    reason: str  # why tiling did or did not happen
    manhattan_max: float | None


def _manhattan_steps(mask: np.ndarray) -> np.ndarray | None:
    """Minimal |dy|+|dx| steps to a source; None when no sources exist."""
    if not bool(mask.any()):
        return None
    # cdt with the "cityblock" metric gives exact Manhattan step counts.
    return ndimage.distance_transform_cdt(~mask, metric="taxicab").astype(np.int64)


def plan_tiles(
    shape: tuple[int, int],
    spacing_y: float,
    spacing_x: float,
    target_cells: int,
    mask: np.ndarray | None = None,
    manhattan: np.ndarray | None = None,
) -> TilingReport:
    """Compute padded windows covering the array.

    The halo is derived from the per-tile Manhattan bound when the mask is
    supplied; otherwise a conservative full-array bound is used.
    """
    h, w = shape
    diag = math.hypot(spacing_y, spacing_x)

    if manhattan is None and mask is not None:
        manhattan = _manhattan_steps(mask)
    if manhattan is None:
        # No sources: every pixel is +inf; a single window is enough but
        # keep tiles bounded in memory.
        windows = _grid_windows(h, w, target_cells, 0)
        return TilingReport(windows, "no_sources", None)

    band_h = max(1, min(h, math.ceil(target_cells / max(1, w))))
    windows: list[TileWindow] = []
    for by0 in range(0, h, band_h):
        by1 = min(h, by0 + band_h)
        windows.extend(_band_column_windows(
            h, w, by0, by1, target_cells, manhattan, diag,
            spacing_y, spacing_x,
        ))
    return TilingReport(windows, "tiled", float(diag * int(manhattan.max())))


def _grid_windows(h: int, w: int, target_cells: int, halo: int) -> list[TileWindow]:
    band_h = max(1, min(h, math.ceil(target_cells / max(1, w))))
    wins: list[TileWindow] = []
    for by0 in range(0, h, band_h):
        by1 = min(h, by0 + band_h)
        wins.append(
            TileWindow(
                max(0, by0 - halo), max(0, -halo),
                min(h, by1 + halo), min(w, w + halo),
                by0, 0, by1, w,
            )
        )
    return wins


def _halo_cells(d_max: float, pitch: float) -> int:
    """Cells to include so every source within ``d_max`` is inside the window.

    A source in cell k has physical offset ``k*pitch``. We must include it
    whenever ``k*pitch <= d_max`` (equality included, otherwise an
    equidistant source just outside the window could win the tie rule).
    """
    if d_max <= 0.0:
        return 0
    return int(math.floor(d_max / pitch)) + 1


def _band_column_windows(
    h: int, w: int, by0: int, by1: int, target_cells: int,
    manhattan: np.ndarray, diag: float,
    sy: float, sx: float,
) -> list[TileWindow]:
    """Split a row band into column tiles with per-tile halos."""
    inner_w = max(1, min(w, math.ceil(target_cells / max(1, by1 - by0))))
    wins: list[TileWindow] = []
    for bx0 in range(0, w, inner_w):
        bx1 = min(w, bx0 + inner_w)
        m_max = int(manhattan[by0:by1, bx0:bx1].max())
        d_max = diag * m_max
        halo_y = _halo_cells(d_max, sy)
        halo_x = _halo_cells(d_max, sx)
        wins.append(TileWindow(
            max(0, by0 - halo_y), max(0, bx0 - halo_x),
            min(h, by1 + halo_y), min(w, bx1 + halo_x),
            by0, bx0, by1, bx1,
        ))
    return wins


def tiled_edt(
    mask: np.ndarray,
    spacing_y: float,
    spacing_x: float,
    target_cells: int,
    force: bool = False,
) -> tuple[EDTResult, TilingReport]:
    """Run the exact EDT, tiling when the raster is large.

    ``force`` bypasses the size threshold (used by tests on small rasters).
    """
    h, w = mask.shape
    manhattan = _manhattan_steps(mask)
    if not force and h * w <= target_cells:
        report = TilingReport([], "below_threshold", None)
        return edt(mask, spacing_y, spacing_x), report

    if manhattan is None:
        # No sources: kernel handles it per window; stitch +inf outputs.
        report = plan_tiles((h, w), spacing_y, spacing_x, target_cells, mask, None)
        dist = np.full((h, w), np.inf, dtype=np.float64)
        ny = np.full((h, w), -1, dtype=np.int64)
        nx = np.full((h, w), -1, dtype=np.int64)
        ties = np.zeros((h, w), dtype=bool)
        return EDTResult(dist, ny, nx, ties, spacing_y, spacing_x, False), report

    report = plan_tiles(
        (h, w), spacing_y, spacing_x, target_cells, mask, manhattan
    )
    dist = np.empty((h, w), dtype=np.float64)
    ny = np.empty((h, w), dtype=np.int64)
    nx = np.empty((h, w), dtype=np.int64)
    ties = np.zeros((h, w), dtype=bool)
    for win in report.windows:
        sub = mask[win.y0:win.y1, win.x0:win.x1]
        r = edt(sub, spacing_y, spacing_x)
        iy0, iy1 = win.inner_y0 - win.y0, win.inner_y1 - win.y0
        ix0, ix1 = win.inner_x0 - win.x0, win.inner_x1 - win.x0
        dist[win.inner_y0:win.inner_y1, win.inner_x0:win.inner_x1] = (
            r.distances[iy0:iy1, ix0:ix1]
        )
        ny[win.inner_y0:win.inner_y1, win.inner_x0:win.inner_x1] = (
            r.nearest_y[iy0:iy1, ix0:ix1] + win.y0
        )
        nx[win.inner_y0:win.inner_y1, win.inner_x0:win.inner_x1] = (
            r.nearest_x[iy0:iy1, ix0:ix1] + win.x0
        )
        ties[win.inner_y0:win.inner_y1, win.inner_x0:win.inner_x1] = (
            r.ties[iy0:iy1, ix0:ix1]
        )
    return EDTResult(
        dist, ny, nx, ties, spacing_y, spacing_x, True
    ), report
