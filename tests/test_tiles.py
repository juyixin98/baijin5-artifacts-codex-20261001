"""Tiled scheduler tests: tiled result must equal the full-image reference,
including strokes that cross tile boundaries."""

from __future__ import annotations

import numpy as np

from app import topology
from app.kernel import thin
from app.tiles import TiledThinner, plan_tiles


def test_plan_tiles_covers_image_exactly():
    jobs = plan_tiles(20, 25, 8)
    covered = np.zeros((20, 25), dtype=int)
    for j in jobs:
        covered[j.row0 : j.row1, j.col0 : j.col1] += 1
    assert (covered == 1).all()  # no overlap, no gap


def test_tiled_equals_full_on_ring(ring):
    full = thin(ring)
    tiled = TiledThinner(tile_size=4).thin(ring)
    np.testing.assert_array_equal(tiled.skeleton, full.skeleton)
    assert tiled.total_deleted == full.total_deleted
    assert len(tiled.rounds) == len(full.rounds)
    assert tiled.tile_count == 16  # 15x15 with tile 4 -> 4x4 grid
    assert tiled.halo_exchanges == 2 * len(tiled.rounds)


def test_tiled_equals_full_on_cross_tile_stroke(cross_tile_stroke):
    full = thin(cross_tile_stroke)
    tiled = TiledThinner(tile_size=8).thin(cross_tile_stroke)
    np.testing.assert_array_equal(tiled.skeleton, full.skeleton)
    # The stroke crosses tile boundaries; its skeleton must stay connected.
    assert topology.count_foreground_components(tiled.skeleton) == 1
    assert int(topology.endpoint_pixels(tiled.skeleton).sum()) == 2


def test_tiled_equals_full_on_fork_with_multithreading(fork):
    full = thin(fork)
    tiled = TiledThinner(tile_size=5, workers=4).thin(fork)
    np.testing.assert_array_equal(tiled.skeleton, full.skeleton)
    assert int(topology.endpoint_pixels(tiled.skeleton).sum()) == 3


def test_tiled_converges_globally_not_per_tile(thin_bridge):
    # A 1-px bridge: nothing to delete anywhere; scheduler must still run one
    # full confirming round and stop.
    tiled = TiledThinner(tile_size=3).thin(thin_bridge)
    np.testing.assert_array_equal(tiled.skeleton, thin_bridge)
    assert tiled.total_deleted == 0
    assert len(tiled.rounds) == 1
