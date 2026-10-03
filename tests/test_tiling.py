"""Tile grid tests: irregular sizes, partition guarantees, halo windows."""

from __future__ import annotations

import numpy as np
import pytest

from tileconv.errors import InvalidSpecError
from tileconv.tiling import TileGrid


class TestGridShape:
    def test_even_division(self):
        grid = TileGrid((64, 48), (16, 16))
        assert len(grid) == 4 * 3
        shapes = {t.shape for t in grid}
        assert shapes == {(16, 16)}

    def test_irregular_edges(self):
        grid = TileGrid((103, 57), (32, 32))
        # rows: 32,32,32,7 -> 4; cols: 32,25 -> 2
        assert len(grid) == 8
        shapes = sorted({t.shape for t in grid})
        assert shapes == [(7, 25), (7, 32), (32, 25), (32, 32)]

    def test_tile_larger_than_image(self):
        grid = TileGrid((10, 8), (64, 64))
        assert len(grid) == 1
        assert grid.tiles[0].shape == (10, 8)

    def test_rejects_nonpositive(self):
        with pytest.raises(InvalidSpecError):
            TileGrid((0, 8), (4, 4))
        with pytest.raises(InvalidSpecError):
            TileGrid((8, 8), (0, 4))


class TestPartitionGuarantee:
    @pytest.mark.parametrize("shape,tile", [
        ((103, 57), (32, 32)),
        ((1, 1), (1, 1)),
        ((5, 7), (2, 3)),
        ((256, 256), (256, 256)),
        ((31, 97), (7, 100)),
    ])
    def test_tiles_form_disjoint_complete_cover(self, shape, tile):
        grid = TileGrid(shape, tile)
        covered = np.zeros(shape, dtype=np.int32)
        for t in grid:
            covered[t.row0 : t.row1, t.col0 : t.col1] += 1
        # every pixel covered exactly once: no overlap, no gap
        assert int(covered.min()) == 1
        assert int(covered.max()) == 1

    def test_row_major_order_and_ids(self):
        grid = TileGrid((4, 6), (2, 2))
        ids = [t.tile_id for t in grid]
        assert ids == list(range(6))
        assert (grid.tiles[1].row0, grid.tiles[1].col0) == (0, 2)


class TestReadWindow:
    def test_halo_expands_window(self):
        grid = TileGrid((100, 100), (50, 50))
        tile = grid.tiles[0]  # rows 0..50, cols 0..50
        r0, r1, c0, c1 = grid.read_window(tile, halo=(2, 2, 1, 3))
        assert (r0, r1, c0, c1) == (-2, 52, -1, 53)

    def test_window_size_matches_kernel_support(self):
        # read window = tile + halo; valid correlation output == tile shape
        grid = TileGrid((100, 100), (50, 50))
        tile = grid.tiles[3]  # bottom-right
        halo = (3, 1, 0, 4)  # kh-1 = 4, kw-1 = 4
        r0, r1, c0, c1 = grid.read_window(tile, halo)
        assert (r1 - r0) - (halo[0] + halo[1]) == tile.row1 - tile.row0
        assert (c1 - c0) - (halo[2] + halo[3]) == tile.col1 - tile.col0
