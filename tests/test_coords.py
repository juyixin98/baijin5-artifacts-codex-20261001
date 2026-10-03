"""Level geometry and the fixed pixel-center mapping, incl. odd sizes."""

import pytest

from pyramid_service.coords import (
    auto_level_count,
    level0_to_pixel_center,
    level_dims,
    pixel_center_to_level0,
    tile_bounds,
    tile_grid,
)
from pyramid_service.errors import InputValidationError


def test_level_dims_never_drop_odd_rows_or_columns():
    assert level_dims(7, 5, 0) == (7, 5)
    assert level_dims(7, 5, 1) == (4, 3)
    assert level_dims(7, 5, 2) == (2, 2)
    assert level_dims(7, 5, 3) == (1, 1)
    # Beyond 1x1 the level stays 1x1 — nothing is ever dropped.
    assert level_dims(7, 5, 4) == (1, 1)


def test_level_dims_even():
    assert level_dims(64, 48, 1) == (32, 24)
    assert level_dims(64, 48, 2) == (16, 12)


def test_auto_level_count_ends_at_1x1():
    assert auto_level_count(7, 5) == 4  # 7x5, 4x3, 2x2, 1x1
    assert auto_level_count(1, 1) == 1
    assert auto_level_count(63, 47) == 7


def test_pixel_center_mapping_roundtrip():
    for level in range(5):
        for coord in (0, 1, 3, 17, 1023):
            back = level0_to_pixel_center(pixel_center_to_level0(coord, level), level)
            assert back == pytest.approx(coord, abs=1e-12)


def test_pixel_center_mapping_is_fixed_across_levels():
    # Level-0 pixel centers are at integer coordinates.
    assert pixel_center_to_level0(0, 0) == 0.0
    assert pixel_center_to_level0(6, 0) == 6.0
    # Level-1 centers sit at 0.5, 2.5, 4.5, ... — pinned literal values.
    assert pixel_center_to_level0(0, 1) == 0.5
    assert pixel_center_to_level0(1, 1) == 2.5
    # Odd edge: width 7 -> level 1 has 4 pixels; the last center (x=3) is at
    # 6.5, i.e. it may extend past the last source center (6.0) by < 1/2
    # level pixel.  This pins the declared mapping for odd sizes.
    assert pixel_center_to_level0(3, 1) == 6.5
    # Same center seen from two levels must map to the same level-0 point:
    # level-2 pixel 1 covers level-1 pixels 2..3, i.e. level-1 coord 2.5.
    assert pixel_center_to_level0(1, 2) == pytest.approx(
        pixel_center_to_level0(2.5, 1)
    )


def test_tile_grid_and_edge_clipping():
    assert tile_grid(20, 14, 8) == (3, 2)
    assert tile_bounds(2, 1, 8, 20, 14) == (16, 8, 4, 6)  # clipped, not padded
    assert tile_bounds(0, 0, 8, 20, 14) == (0, 0, 8, 8)


def test_invalid_geometry_inputs():
    with pytest.raises(InputValidationError):
        level_dims(0, 5, 0)
    with pytest.raises(InputValidationError):
        level_dims(5, 5, -1)
    with pytest.raises(InputValidationError):
        pixel_center_to_level0(0, -1)
    with pytest.raises(InputValidationError):
        tile_grid(10, 10, 0)
    with pytest.raises(InputValidationError):
        tile_bounds(5, 0, 8, 20, 14)
    with pytest.raises(InputValidationError):
        auto_level_count(0, 5)
