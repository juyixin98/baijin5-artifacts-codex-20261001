"""Tiling tests: tiled execution must equal direct execution and the
brute-force oracle exactly — including when the nearest source lives
several tiles away (cross-tile halo correctness)."""

import numpy as np
import pytest

from edt_service.kernel import NO_SOURCE, edt2d
from edt_service.reference import brute_force_edt
from edt_service.tiling import edt2d_tiled


def _assert_same(dist_a, lab_a, dist_b, lab_b):
    assert np.array_equal(np.isinf(dist_a), np.isinf(dist_b))
    assert np.allclose(dist_a, dist_b, rtol=1e-12, atol=1e-12)
    assert np.array_equal(lab_a, lab_b)


@pytest.mark.parametrize("seed", range(15))
def test_tiled_equals_direct_random(seed):
    rng = np.random.default_rng(seed)
    h = int(rng.integers(9, 45))
    w = int(rng.integers(9, 45))
    mask = rng.random((h, w)) < rng.choice([0.02, 0.2, 0.6])
    spacing = [(1.0, 1.0), (2.0, 1.0), (0.5, 3.0)][seed % 3]
    d_direct, l_direct = edt2d(mask, spacing)
    d_tiled, l_tiled, reports = edt2d_tiled(mask, spacing, tile_size=8)
    assert len(reports) > 1  # actually exercised multiple tiles
    _assert_same(d_direct, l_direct, d_tiled, l_tiled)


def test_cross_tile_nearest_source_not_missed():
    # Single source in the far corner: every other tile's nearest source
    # is many tiles away. A fixed-halo scheme would fail here.
    mask = np.zeros((40, 40), dtype=bool)
    mask[0, 0] = True
    d_tiled, l_tiled, _ = edt2d_tiled(mask, (1.0, 1.0), tile_size=8)
    d_ref, l_ref = brute_force_edt(mask, (1.0, 1.0))
    _assert_same(d_tiled, l_tiled, d_ref, l_ref)
    assert l_tiled[39, 39] == 0  # far corner still labels the only source


def test_sources_clustered_in_one_tile():
    mask = np.zeros((30, 60), dtype=bool)
    mask[2, 3] = True
    mask[5, 7] = True
    d_tiled, l_tiled, _ = edt2d_tiled(mask, (1.0, 1.0), tile_size=4)
    d_ref, l_ref = brute_force_edt(mask, (1.0, 1.0))
    _assert_same(d_tiled, l_tiled, d_ref, l_ref)


def test_tile_without_local_sources_uses_corner_bound():
    # Sources only in the leftmost tile column; tiles to the right have no
    # local source and must fall back to the corner-derived halo bound.
    mask = np.zeros((20, 50), dtype=bool)
    mask[10, 2] = True
    d_tiled, l_tiled, reports = edt2d_tiled(mask, (1.0, 1.0), tile_size=5)
    d_direct, l_direct = edt2d(mask, (1.0, 1.0))
    _assert_same(d_tiled, l_tiled, d_direct, l_direct)
    assert any(r.upper_bound > 0 for r in reports)


def test_tiled_long_thin():
    rng = np.random.default_rng(7)
    for shape in [(1, 250), (250, 1), (3, 400)]:
        mask = rng.random(shape) < 0.05
        mask[0, 0] = True
        d_tiled, l_tiled, _ = edt2d_tiled(mask, (1.0, 1.0), tile_size=16)
        d_ref, l_ref = brute_force_edt(mask, (1.0, 1.0))
        _assert_same(d_tiled, l_tiled, d_ref, l_ref)


def test_tiled_anisotropic_spacing():
    rng = np.random.default_rng(11)
    mask = rng.random((35, 28)) < 0.1
    d_tiled, l_tiled, _ = edt2d_tiled(mask, (3.0, 0.5), tile_size=6)
    d_ref, l_ref = brute_force_edt(mask, (3.0, 0.5))
    _assert_same(d_tiled, l_tiled, d_ref, l_ref)


def test_tiled_empty_raster():
    dist, labels, reports = edt2d_tiled(np.zeros((10, 10), bool), (1.0, 1.0), 4)
    assert np.isinf(dist).all()
    assert (labels == NO_SOURCE).all()
    assert reports == []


def test_tiled_tie_break_consistent_with_direct():
    # Symmetric source layout: maximal equidistant pixels across tiles.
    mask = np.zeros((16, 16), dtype=bool)
    mask[0, 0] = mask[0, 15] = mask[15, 0] = mask[15, 15] = True
    d_direct, l_direct = edt2d(mask, (1.0, 1.0))
    d_tiled, l_tiled, _ = edt2d_tiled(mask, (1.0, 1.0), tile_size=4)
    _assert_same(d_direct, l_direct, d_tiled, l_tiled)


def test_tile_reports_cover_every_pixel():
    mask = np.random.default_rng(3).random((23, 17)) < 0.3
    _, _, reports = edt2d_tiled(mask, (1.0, 1.0), tile_size=8)
    covered = np.zeros((23, 17), dtype=int)
    for r in reports:
        covered[r.row0 : r.row1, r.col0 : r.col1] += 1
    assert (covered == 1).all()
