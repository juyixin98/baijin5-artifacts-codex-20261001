"""Tiled thinning driver with halo exchange and synchronous convergence.

Tiles never thin independently. Each sub-iteration proceeds as:

1. BARRIER-IN: every tile reads its interior plus a 1-pixel halo copied
   from the SAME global prior state (zero padding at the image border).
2. Each tile computes its deletion candidates from that local view.
3. BARRIER-OUT: all candidates are OR-combined and applied to the global
   state at once, only after every tile has finished computing.

Because deletion decisions depend only on the prior state, the tiled
result is bit-identical to running the kernel on the full image; the
test-suite asserts this on strokes that cross tile boundaries.
Convergence is global: the loop stops only when a full round deletes
zero pixels across ALL tiles.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.kernel import deletion_mask


@dataclass(frozen=True)
class Tile:
    index: int
    row0: int
    col0: int
    row1: int  # exclusive
    col1: int  # exclusive

    @property
    def shape(self) -> tuple[int, int]:
        return (self.row1 - self.row0, self.col1 - self.col0)


def partition(shape: tuple[int, int], tile_size: int) -> list[Tile]:
    """Split ``shape`` into a row-major grid of tiles of at most tile_size."""
    if tile_size < 1:
        raise ValueError(f"tile_size must be >= 1, got {tile_size}")
    rows, cols = shape
    tiles: list[Tile] = []
    index = 0
    for r0 in range(0, rows, tile_size):
        for c0 in range(0, cols, tile_size):
            tiles.append(
                Tile(index=index, row0=r0, col0=c0,
                     row1=min(r0 + tile_size, rows), col1=min(c0 + tile_size, cols))
            )
            index += 1
    return tiles


def _halo_view(state: np.ndarray, tile: Tile, halo: int) -> np.ndarray:
    """Tile interior plus halo, zero-padded where the halo leaves the image."""
    rows, cols = state.shape
    r0, c0 = tile.row0 - halo, tile.col0 - halo
    r1, c1 = tile.row1 + halo, tile.col1 + halo
    view = np.zeros((r1 - r0, c1 - c0), dtype=np.uint8)
    sr0, sc0 = max(r0, 0), max(c0, 0)
    sr1, sc1 = min(r1, rows), min(c1, cols)
    view[sr0 - r0 : sr1 - r0, sc0 - c0 : sc1 - c0] = state[sr0:sr1, sc0:sc1]
    return view


@dataclass(frozen=True)
class TiledThinningResult:
    skeleton: np.ndarray
    rounds: int
    deletions_per_round: tuple[tuple[int, int], ...]
    converged: bool
    tile_count: int
    tile_size: int
    halo_width: int


def thin_tiled(
    image: np.ndarray,
    tile_size: int,
    max_rounds: int = 256,
    halo_width: int = 1,
) -> TiledThinningResult:
    """Thin by tiles with halo exchange; synchronised with the global state."""
    current = np.ascontiguousarray(image).astype(np.uint8).copy()
    tiles = partition(current.shape, tile_size)
    deletions: list[tuple[int, int]] = []
    converged = False
    for _ in range(max_rounds):
        round_pair = [0, 0]
        for sub in (1, 2):
            prior = current  # same prior state for every tile this sub-iteration
            combined = np.zeros_like(prior, dtype=bool)
            for tile in tiles:
                view = _halo_view(prior, tile, halo_width)
                mask = deletion_mask(view, subiteration=sub)
                h = halo_width
                interior = mask[h : h + tile.shape[0], h : h + tile.shape[1]]
                combined[tile.row0 : tile.row1, tile.col0 : tile.col1] |= interior
            current = prior & ~combined  # synchronous apply after the barrier
            round_pair[sub - 1] = int(combined.sum())
        pair = (round_pair[0], round_pair[1])
        deletions.append(pair)
        if pair == (0, 0):
            converged = True
            break
    return TiledThinningResult(
        skeleton=current.astype(np.uint8),
        rounds=len(deletions),
        deletions_per_round=tuple(deletions),
        converged=converged,
        tile_count=len(tiles),
        tile_size=tile_size,
        halo_width=halo_width,
    )
