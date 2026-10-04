"""Larger randomized stress tests for the tiled path (still exact-checked).

Grids are big enough that halos demonstrably cross many tiles, but small
enough to cross-check with SciPy (the independent third implementation).
"""
from __future__ import annotations

import numpy as np
import pytest
from scipy import ndimage

from app.kernel import edt
from app.tiling import tiled_edt


@pytest.mark.parametrize("seed", [11, 22, 33])
@pytest.mark.parametrize("spacing", [(1.0, 1.0), (0.25, 4.0), (3.0, 0.3)])
def test_medium_tiled_vs_scipy_and_direct(seed, spacing):
    rng = np.random.default_rng(seed)
    h, w = 80, 53
    mask = np.zeros((h, w), dtype=bool)
    # Very sparse: sources only in a handful of pixels, so halos are huge.
    ys = rng.integers(0, h, size=5)
    xs = rng.integers(0, w, size=5)
    mask[ys, xs] = True
    sy, sx = spacing

    tiled, report = tiled_edt(mask, sy, sx, target_cells=300, force=True)
    direct = edt(mask, sy, sx)
    sd, _ = ndimage.distance_transform_edt(
        ~mask, sampling=(sy, sx), return_indices=True
    )

    assert len(report.windows) > 1
    assert np.allclose(tiled.distances, direct.distances, rtol=1e-12)
    assert np.array_equal(tiled.nearest_y, direct.nearest_y)
    assert np.array_equal(tiled.nearest_x, direct.nearest_x)
    assert np.array_equal(tiled.ties, direct.ties)
    assert np.allclose(tiled.distances, sd, rtol=1e-10, atol=1e-9)


def test_tiled_single_corner_source_halo_covers_all():
    """One source at (0,0): tiles at the far corner must still find it."""
    h, w = 40, 60
    mask = np.zeros((h, w), dtype=bool)
    mask[0, 0] = True
    tiled, _ = tiled_edt(mask, 1.0, 1.0, target_cells=200, force=True)
    far = tiled.distances[h - 1, w - 1]
    assert far == pytest.approx(float(np.hypot(h - 1, w - 1)))
    assert (int(tiled.nearest_y[h - 1, w - 1]),
            int(tiled.nearest_x[h - 1, w - 1])) == (0, 0)


def test_tiled_exact_halo_boundary_equality():
    """A source exactly d_max away from an interior edge pixel must be seen.

    Layout (1-D along x, one row): source at x=0 and x=2k. For a tile whose
    interior starts at k, both sources are equidistant and the tie rule
    must select x=0 even when it sits on the halo boundary.
    """
    n = 21
    mask = np.zeros((1, n), dtype=bool)
    mask[0, 0] = True
    mask[0, n - 1] = True
    tiled, _ = tiled_edt(mask, 1.0, 1.0, target_cells=2, force=True)
    mid = n // 2
    assert tiled.distances[0, mid] == pytest.approx(float(mid))
    assert int(tiled.nearest_x[0, mid]) == 0
    assert bool(tiled.ties[0, mid]) is True
