"""Tiled execution tests.

The central correctness hazard of tiling is a nearest source living in an
adjacent block. These tests force adversarial source layouts (sources only
at far corners/edges, tiny target tiles so halos span many blocks) and
compare the stitched result pixel-by-pixel against the direct kernel and
the independent brute-force reference.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from app.kernel import edt
from app.reference import brute_force_edt
from app.tiling import plan_tiles, tiled_edt


def _assert_tile_equals_direct(mask, sy, sx, target_cells):
    direct = edt(mask, sy, sx)
    tiled, report = tiled_edt(mask, sy, sx, target_cells, force=True)
    assert report.reason in ("tiled", "no_sources")
    assert len(report.windows) >= 1

    finite = np.isfinite(direct.distances)
    assert np.allclose(tiled.distances[finite], direct.distances[finite],
                       rtol=1e-12, atol=1e-10)
    assert np.array_equal(tiled.nearest_y, direct.nearest_y)
    assert np.array_equal(tiled.nearest_x, direct.nearest_x)
    assert np.array_equal(tiled.ties, direct.ties)
    return tiled, report


def test_multiple_tiles_actually_created():
    """Sanity: a tiny target really forces several windows."""
    mask = np.zeros((6, 6), dtype=bool)
    mask[0, 0] = True
    _, report = tiled_edt(mask, 1.0, 1.0, target_cells=4, force=True)
    assert len(report.windows) > 1


def test_corner_sources_span_many_tiles_isotropic():
    """Worst case: the nearest source for the centre is ~30 cells away and
    tiles are 2x2 — halos must span 15+ blocks without losing it."""
    n = 31
    mask = np.zeros((n, n), dtype=bool)
    mask[0, 0] = mask[0, n - 1] = mask[n - 1, 0] = mask[n - 1, n - 1] = True
    tiled, report = _assert_tile_equals_direct(mask, 1.0, 1.0, target_cells=4)
    # Centre pixel distance to any corner = sqrt(2)*15.
    c = n // 2
    assert tiled.distances[c, c] == pytest.approx(math.hypot(15, 15))
    assert (int(tiled.nearest_y[c, c]), int(tiled.nearest_x[c, c])) == (0, 0)
    # The windows must demonstrably overlap (halos reach across blocks).
    big = [w for w in report.windows
           if (w.inner_y1 - w.inner_y0) < (w.y1 - w.y0)
           or (w.inner_x1 - w.inner_x0) < (w.x1 - w.x0)]
    assert big, "expected halo-expanded windows"


def test_corner_sources_anisotropic():
    """sy=3, sx=0.4: halos in cells must differ strongly per axis."""
    n = 25
    mask = np.zeros((n, n), dtype=bool)
    mask[0, 0] = mask[n - 1, n - 1] = True
    _assert_tile_equals_direct(mask, 3.0, 0.4, target_cells=9)


def test_long_thin_tiled_horizontal():
    mask = np.zeros((1, 60), dtype=bool)
    mask[0, 0] = mask[0, 59] = True
    tiled, _ = _assert_tile_equals_direct(mask, 1.0, 1.0, target_cells=5)
    # Tie would be at x=29.5 which is not a pixel; integer pixels are
    # strictly assigned to one side: x=29 -> source 0 (29 < 30),
    # x=30 -> source 59 (29 < 30).
    assert tiled.distances[0, 29] == pytest.approx(29.0)
    assert tiled.distances[0, 30] == pytest.approx(29.0)
    assert int(tiled.nearest_x[0, 29]) == 0
    assert int(tiled.nearest_x[0, 30]) == 59


def test_long_thin_tiled_vertical():
    mask = np.zeros((60, 1), dtype=bool)
    mask[0, 0] = mask[59, 0] = True
    _assert_tile_equals_direct(mask, 0.2, 1.0, target_cells=5)


def test_tiled_no_sources():
    mask = np.zeros((10, 10), dtype=bool)
    tiled, report = tiled_edt(mask, 1.0, 1.0, target_cells=9, force=True)
    assert report.reason == "no_sources"
    assert np.isinf(tiled.distances).all()
    assert (tiled.nearest_y == -1).all()
    assert (tiled.nearest_x == -1).all()


def test_tiled_all_sources():
    mask = np.ones((7, 7), dtype=bool)
    tiled, _ = _assert_tile_equals_direct(mask, 1.0, 1.0, target_cells=4)
    assert np.all(tiled.distances == 0.0)


def test_tiled_matches_brute_force_adversarial():
    """Tiled vs the INDEPENDENT reference on random + frame layouts."""
    rng = np.random.default_rng(123)
    for shape in [(13, 9), (1, 20), (17, 1)]:
        for p in (0.1, 0.6):
            mask = rng.random(shape) < p
            tiled, _ = tiled_edt(mask, 1.3, 0.6, target_cells=8, force=True)
            ref = brute_force_edt(mask, 1.3, 0.6)
            h, w = shape
            for y in range(h):
                for x in range(w):
                    rv = ref.distances[y][x]
                    if math.isinf(rv):
                        assert math.isinf(tiled.distances[y, x])
                        continue
                    assert tiled.distances[y, x] == pytest.approx(
                        rv, rel=1e-9, abs=1e-10
                    )
                    assert (int(tiled.nearest_y[y, x]),
                            int(tiled.nearest_x[y, x])) == (
                        ref.nearest_y[y][x], ref.nearest_x[y][x]
                    )


def test_tiled_interiors_cover_array_exactly_once():
    """No interior pixel may be missed or double-written with disagreement."""
    mask = np.zeros((12, 12), dtype=bool)
    mask[2, 3] = mask[9, 8] = True
    report = plan_tiles((12, 12), 1.0, 1.0, 16, mask=mask)
    covered = np.zeros((12, 12), dtype=int)
    for win in report.windows:
        covered[win.inner_y0:win.inner_y1,
                win.inner_x0:win.inner_x1] += 1
    assert (covered == 1).all()
