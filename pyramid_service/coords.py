"""Level geometry and the fixed pixel-center coordinate mapping.

All functions here are pure.  Widths/heights are in pixels; arrays elsewhere
are indexed ``[y, x]`` while this module speaks ``(width, height)``.
"""

from __future__ import annotations

from typing import Tuple

from .errors import InputValidationError


def level_dims(width: int, height: int, level: int) -> Tuple[int, int]:
    """Dimensions of ``level``: ``ceil(dim / 2**level)`` per axis.

    Ceiling division is what guarantees odd sizes never lose a row/column.
    """
    if level < 0:
        raise InputValidationError(f"level must be >= 0, got {level}")
    if width <= 0 or height <= 0:
        raise InputValidationError(
            f"source dimensions must be positive, got {width}x{height}"
        )
    shift = 1 << level
    return (width + shift - 1) // shift, (height + shift - 1) // shift


def pixel_center_to_level0(coord: float, level: int) -> float:
    """Map a level-``level`` pixel index to the level-0 coordinate of its center."""
    if level < 0:
        raise InputValidationError(f"level must be >= 0, got {level}")
    return (coord + 0.5) * (1 << level) - 0.5


def level0_to_pixel_center(coord: float, level: int) -> float:
    """Inverse of :func:`pixel_center_to_level0`."""
    if level < 0:
        raise InputValidationError(f"level must be >= 0, got {level}")
    return (coord + 0.5) / (1 << level) - 0.5


def tile_grid(width: int, height: int, tile_size: int) -> Tuple[int, int]:
    """Number of tiles along x and y for a level of ``width`` x ``height``."""
    if tile_size <= 0:
        raise InputValidationError(f"tile_size must be positive, got {tile_size}")
    return (width + tile_size - 1) // tile_size, (height + tile_size - 1) // tile_size


def tile_bounds(
    tx: int, ty: int, tile_size: int, width: int, height: int
) -> Tuple[int, int, int, int]:
    """``(x0, y0, w, h)`` of tile ``(tx, ty)``; edge tiles are clipped, not padded."""
    if tile_size <= 0:
        raise InputValidationError(f"tile_size must be positive, got {tile_size}")
    x0 = tx * tile_size
    y0 = ty * tile_size
    if x0 >= width or y0 >= height or tx < 0 or ty < 0:
        raise InputValidationError(
            f"tile ({tx}, {ty}) outside grid for level of {width}x{height}"
        )
    return x0, y0, min(tile_size, width - x0), min(tile_size, height - y0)


def auto_level_count(width: int, height: int) -> int:
    """Number of levels (including level 0) down to and including 1x1."""
    if width <= 0 or height <= 0:
        raise InputValidationError(
            f"source dimensions must be positive, got {width}x{height}"
        )
    levels = 1
    w, h = width, height
    while w > 1 or h > 1:
        w = (w + 1) // 2
        h = (h + 1) // 2
        levels += 1
    return levels
