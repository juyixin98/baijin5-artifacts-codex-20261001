"""Region reads: exact cross-tile stitching against full-level references,
plus the distinguishable failure categories (input / not-found / resource)."""

import numpy as np
import pytest
from PIL import Image

from pyramid_service.builder import BuildSpec, SourceSpec, build_pyramid
from pyramid_service.contracts import RegionSpec
from pyramid_service.errors import (
    InputValidationError,
    NotFoundError,
    ResourceExhaustedError,
)
from pyramid_service.patterns import gradient, noise
from pyramid_service.region import read_region

from conftest import cascade_reference


def _build(store, settings, pattern_array, tile_size, levels, kernel="area",
           pid="p"):
    h, w = pattern_array.shape[:2]
    path = settings.store_dir.parent / f"{pid}_src.npy"
    np.save(path, pattern_array)
    spec = BuildSpec(
        pyramid_id=pid,
        source=SourceSpec(kind="npy", path=str(path)),
        tile_size=tile_size,
        levels=levels,
        kernel=kernel,
    )
    return build_pyramid(store, spec, settings)


def test_cross_tile_region_matches_full_level_reference(store, settings):
    src = gradient(14, 20)  # level 1 is 7x10 -> crosses the 8px tile boundary
    _build(store, settings, src, tile_size=8, levels=3)
    ref = cascade_reference(src, 3, "area")

    # Region deliberately straddles tiles (0,0) and (1,0) at level 1.
    out = read_region(store, "p", RegionSpec(level=1, x=6, y=2, w=4, h=4))
    assert out.shape == (4, 4, 1)
    np.testing.assert_allclose(out, ref[1][2:6, 6:10], atol=1e-12)

    # Full-level read (all tiles) equals the direct kernel cascade.
    full = read_region(store, "p", RegionSpec(level=2, x=0, y=0, w=5, h=4))
    np.testing.assert_allclose(full, ref[2], atol=1e-12)


def test_region_level1_matches_pillow_box(store, settings):
    src = noise(16, 32, seed=5)
    _build(store, settings, src, tile_size=8, levels=2)
    out = read_region(store, "p", RegionSpec(level=1, x=0, y=0, w=16, h=8))
    ref = np.asarray(
        Image.fromarray(src[..., 0].astype(np.float32), mode="F").resize(
            (16, 8), Image.BOX
        )
    )
    np.testing.assert_allclose(out[..., 0], ref, atol=1e-3)


def test_gaussian_kernel_stitches_identically_to_full_array(store, settings):
    src = noise(14, 20, seed=11)
    _build(store, settings, src, tile_size=8, levels=2, kernel="gaussian")
    ref = cascade_reference(src, 2, "gaussian")
    out = read_region(store, "p", RegionSpec(level=1, x=0, y=0, w=10, h=7))
    np.testing.assert_allclose(out, ref[1], atol=1e-12)


def test_region_is_clipped_to_level_bounds(store, settings):
    src = gradient(10, 10)
    _build(store, settings, src, tile_size=4, levels=2)
    out = read_region(store, "p", RegionSpec(level=0, x=8, y=8, w=10, h=10))
    assert out.shape == (2, 2, 1)
    np.testing.assert_allclose(out, src[8:10, 8:10], atol=1e-12)


def test_region_straddling_four_tiles(store, settings):
    # 2x2 tile-corner stitch: the region crosses both tile boundaries at once.
    src = noise(32, 32, seed=31)
    _build(store, settings, src, tile_size=8, levels=2)
    ref = cascade_reference(src, 2, "area")
    out = read_region(store, "p", RegionSpec(level=1, x=6, y=6, w=4, h=4))
    assert out.shape == (4, 4, 1)
    np.testing.assert_allclose(out, ref[1][6:10, 6:10], atol=1e-12)


def test_region_failure_categories(store, settings):
    src = gradient(10, 10)
    _build(store, settings, src, tile_size=4, levels=2)

    with pytest.raises(InputValidationError):  # non-positive size
        read_region(store, "p", RegionSpec(level=0, x=0, y=0, w=0, h=4))
    with pytest.raises(InputValidationError):  # negative origin
        read_region(store, "p", RegionSpec(level=0, x=-1, y=0, w=4, h=4))
    with pytest.raises(InputValidationError):  # fully outside
        read_region(store, "p", RegionSpec(level=0, x=10, y=0, w=4, h=4))
    with pytest.raises(InputValidationError):  # negative level
        read_region(store, "p", RegionSpec(level=-1, x=0, y=0, w=4, h=4))
    with pytest.raises(NotFoundError):  # level never published
        read_region(store, "p", RegionSpec(level=7, x=0, y=0, w=2, h=2))
    with pytest.raises(NotFoundError):  # pyramid does not exist
        read_region(store, "ghost", RegionSpec(level=0, x=0, y=0, w=2, h=2))
    with pytest.raises(ResourceExhaustedError):  # above the pixel limit
        read_region(store, "p", RegionSpec(level=0, x=0, y=0, w=10, h=10),
                    max_pixels=50)
