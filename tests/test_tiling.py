"""Tiling tests: exact cover, no double writes, tiled == references."""
from __future__ import annotations

import numpy as np
import pytest

from app.contract import BoundaryMode, KernelSpec
from app.fixtures import (
    impulse_image,
    seeded_noise_image,
    separable_gaussian,
    step_edge_image,
    tagged_border_image,
)
from app.kernels import filter_direct, filter_separable_direct, filter_window
from app.reference import scipy_reference
from app.tiling import plan_tiles, validate_tiling

ALL_MODES = [BoundaryMode.MIRROR, BoundaryMode.CONSTANT, BoundaryMode.PERIODIC]


def tiled_filter(img, spec, tile_hw, mode, cval=0.0, separable=False):
    """Run the filter tile by tile into a fresh output array."""
    from app.kernels import filter_separable_window

    tiles = plan_tiles(img.shape, tile_hw)
    validate_tiling(tiles, img.shape)
    out = np.full(img.shape, np.nan)
    writes = np.zeros(img.shape, dtype=np.int64)
    for t in tiles:
        if separable:
            block = filter_separable_window(img, spec, (t.y0, t.x0),
                                            (t.h, t.w), mode, cval)
        else:
            block = filter_window(img, spec, (t.y0, t.x0), (t.h, t.w), mode, cval)
        out[t.y0 : t.y0 + t.h, t.x0 : t.x0 + t.w] = block
        writes[t.y0 : t.y0 + t.h, t.x0 : t.x0 + t.w] += 1
    return out, writes


class TestTilePlan:
    def test_irregular_tiles_cover_exactly(self):
        tiles = plan_tiles((103, 97), (32, 32))
        validate_tiling(tiles, (103, 97))
        # 4 x 4 grid; last row height 103-96=7, last col width 97-96=1
        assert len(tiles) == 16
        assert tiles[-1].h == 7 and tiles[-1].w == 1
        assert sum(t.area for t in tiles) == 103 * 97

    def test_tile_larger_than_image(self):
        tiles = plan_tiles((10, 8), (64, 64))
        assert len(tiles) == 1
        validate_tiling(tiles, (10, 8))

    def test_invalid_tile_size_rejected(self):
        with pytest.raises(Exception):
            plan_tiles((10, 10), (0, 4))

    def test_validate_tiling_detects_overlap(self):
        from app.tiling import Tile

        tiles = [Tile(0, 0, 0, 4, 4), Tile(1, 2, 2, 4, 4), Tile(2, 0, 4, 4, 4),
                 Tile(3, 4, 0, 4, 4)]
        # areas sum to 64 == 8*8 but tiles 0 and 1 overlap
        with pytest.raises(Exception, match="overlap|gaps"):
            validate_tiling(tiles, (8, 8))


class TestTiledEqualsDirect:
    @pytest.mark.parametrize("mode", ALL_MODES)
    @pytest.mark.parametrize("image", [
        impulse_image((67, 51), pos=(30, 20)),
        step_edge_image((67, 51), axis=1),
        tagged_border_image((67, 51), border=2),
        seeded_noise_image((67, 51), seed=9),
    ], ids=["impulse", "edge", "border", "noise"])
    @pytest.mark.parametrize("kshape", [(3, 3), (4, 4), (31, 31)])
    def test_tiled_matches_direct_and_scipy(self, mode, image, kshape):
        rng = np.random.default_rng(1)
        k = KernelSpec.from_array(rng.standard_normal(kshape))
        tiled, writes = tiled_filter(image, k, (16, 16), mode, cval=0.3)
        # every valid pixel written exactly once: no overlap, no gap
        assert np.all(writes == 1)
        direct = filter_direct(image, k, mode, cval=0.3)
        np.testing.assert_allclose(tiled, direct, atol=1e-12)
        ref = scipy_reference(image, k, mode, cval=0.3)
        np.testing.assert_allclose(tiled, ref, atol=1e-10)

    @pytest.mark.parametrize("mode", ALL_MODES)
    def test_tiled_separable_matches_direct(self, mode):
        img = seeded_noise_image((80, 66), seed=12)
        sep = separable_gaussian(9, 2.0)
        tiled, writes = tiled_filter(img, sep, (24, 24), mode, separable=True)
        assert np.all(writes == 1)
        direct = filter_separable_direct(img, sep, mode)
        np.testing.assert_allclose(tiled, direct, atol=1e-12)
        ref = scipy_reference(img, sep, mode)
        np.testing.assert_allclose(tiled, ref, atol=1e-10)

    def test_even_kernel_halo_not_short_on_anchor_side(self):
        # Even 4x4 kernel, anchor (2,2): needs 2 rows of top context.
        # A halo that is one short on the anchor side shifts the output by
        # one pixel; this catches it on a 1-pixel-wide tile column.
        img = seeded_noise_image((33, 17), seed=21)
        k = KernelSpec.from_array(np.arange(16.0).reshape(4, 4))
        tiled, _ = tiled_filter(img, k, (33, 1), BoundaryMode.MIRROR)
        direct = filter_direct(img, k, BoundaryMode.MIRROR)
        np.testing.assert_allclose(tiled, direct, atol=1e-12)
