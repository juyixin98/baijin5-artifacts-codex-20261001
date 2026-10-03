"""Tile planning.

The output image is partitioned into a grid of non-overlapping tiles.  Each
tile owns exactly its output window; halo context is *read* from neighbours
but never *written*, so the union of tile writes covers the image exactly
once — no valid pixel is written twice.
"""
from __future__ import annotations

from dataclasses import dataclass

from .contract import ContractError


@dataclass(frozen=True)
class Tile:
    """One output window: rows [y0, y0+h), cols [x0, x0+w)."""

    index: int
    y0: int
    x0: int
    h: int
    w: int

    @property
    def area(self) -> int:
        return self.h * self.w


def plan_tiles(
    shape: tuple[int, int], tile_hw: tuple[int, int]
) -> list[Tile]:
    """Partition ``shape`` into tiles of at most ``tile_hw``.

    The last tile in each row/column absorbs the remainder, so tile sizes
    are irregular whenever the image is not an exact multiple of the tile.
    """
    H, W = shape
    th, tw = tile_hw
    if th < 1 or tw < 1:
        raise ContractError(f"tile size must be positive, got {tile_hw}")
    if H < 1 or W < 1:
        raise ContractError(f"image shape must be positive, got {shape}")
    tiles: list[Tile] = []
    idx = 0
    y = 0
    while y < H:
        h = min(th, H - y)
        x = 0
        while x < W:
            w = min(tw, W - x)
            tiles.append(Tile(index=idx, y0=y, x0=x, h=h, w=w))
            idx += 1
            x += w
        y += h
    return tiles


def validate_tiling(tiles: list[Tile], shape: tuple[int, int]) -> None:
    """Prove the tiling is an exact cover: no gaps, no overlaps, in bounds.

    Uses interval arithmetic (O(n log n)), not a rasterised count array, so
    it stays valid for arbitrarily large images.
    """
    H, W = shape
    if not tiles:
        raise ContractError("tiling is empty")
    total_area = 0
    for t in tiles:
        if t.h < 1 or t.w < 1:
            raise ContractError(f"tile {t.index} has non-positive extent")
        if t.y0 < 0 or t.x0 < 0 or t.y0 + t.h > H or t.x0 + t.w > W:
            raise ContractError(
                f"tile {t.index} {(t.y0, t.x0, t.h, t.w)} escapes image {shape}"
            )
        total_area += t.area
    if total_area != H * W:
        raise ContractError(
            f"tile areas sum to {total_area}, expected {H * W}: "
            "tiling overlaps or leaves gaps"
        )
    # Sweep-line overlap check on rectangles (area equality alone does not
    # rule out overlap + gap combinations).
    events: list[tuple[int, int, Tile]] = []
    for t in tiles:
        events.append((t.y0, 1, t))
        events.append((t.y0 + t.h, -1, t))
    events.sort(key=lambda e: (e[0], e[1]))
    active: list[Tile] = []
    for _, kind, t in events:
        if kind == -1:
            active.remove(t)
            continue
        for other in active:
            if t.x0 < other.x0 + other.w and other.x0 < t.x0 + t.w:
                raise ContractError(
                    f"tiles {other.index} and {t.index} overlap"
                )
        active.append(t)
