"""Tiled job execution.

The ICC transform is strictly per-pixel, so splitting an image into tiles and
converting each tile independently must produce a bit-identical result to
converting the whole image at once.  ``tests/test_tiles.py`` asserts exactly
that.  Tiling exists to bound peak memory and to give jobs resumable,
auditable units of work.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contract import ImageData


@dataclass(frozen=True)
class Tile:
    index: int
    y0: int
    x0: int
    y1: int
    x1: int

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def width(self) -> int:
        return self.x1 - self.x0


@dataclass(frozen=True)
class TileRecord:
    tile: Tile
    status: str  # "completed" | "failed"
    detail: str = ""


def plan_tiles(height: int, width: int, tile_size: int) -> list[Tile]:
    """Grid of at most ``tile_size`` x ``tile_size`` tiles, row-major."""
    if tile_size <= 0:
        raise ValueError("tile_size must be positive")
    tiles: list[Tile] = []
    index = 0
    for y0 in range(0, height, tile_size):
        for x0 in range(0, width, tile_size):
            tiles.append(
                Tile(
                    index=index,
                    y0=y0,
                    x0=x0,
                    y1=min(y0 + tile_size, height),
                    x1=min(x0 + tile_size, width),
                )
            )
            index += 1
    return tiles


def run_tiled(image: ImageData, tile_size: int, convert_color) -> tuple[ImageData, list[TileRecord]]:
    """Run ``convert_color`` on every tile and reassemble.

    ``convert_color`` maps a ``(h, w, C)`` uint8 array to a ``(h, w, C')``
    uint8 array.  Alpha is sliced along and reattached untouched.
    """
    image.validate()
    tiles = plan_tiles(image.height, image.width, tile_size)

    out_color: np.ndarray | None = None
    records: list[TileRecord] = []
    for tile in tiles:
        chunk = image.color[tile.y0 : tile.y1, tile.x0 : tile.x1, :]
        try:
            converted = convert_color(chunk)
        except Exception as exc:
            records.append(TileRecord(tile=tile, status="failed", detail=str(exc)))
            raise
        if out_color is None:
            out_color = np.empty(
                (image.height, image.width, converted.shape[2]), dtype=np.uint8
            )
        out_color[tile.y0 : tile.y1, tile.x0 : tile.x1, :] = converted
        records.append(TileRecord(tile=tile, status="completed"))

    if out_color is None:  # zero-area image: nothing ran
        out_color = np.empty((image.height, image.width, 0), dtype=np.uint8)

    # The output mode is decided by the caller (it owns the transform); we
    # keep the input mode here and let the service set the final mode.
    result = ImageData(
        color=out_color,
        mode=image.mode,
        alpha=image.alpha,
        premultiplied=image.premultiplied,
    )
    return result, records
