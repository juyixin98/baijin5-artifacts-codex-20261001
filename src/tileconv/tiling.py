"""Tile grid decomposition.

The image is partitioned into a row-major grid of write regions. Interior
tiles have the requested ``tile_shape``; tiles on the right/bottom edges are
smaller (irregular sizes are first-class, not an error). Each tile reads a
halo-expanded window but writes *only* its own region, so write regions form
a disjoint, complete partition of the image — no pixel is written twice and
none is skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Sequence, Tuple

from .errors import InvalidSpecError


@dataclass(frozen=True)
class Tile:
    tile_id: int
    row0: int
    row1: int
    col0: int
    col1: int

    @property
    def shape(self) -> Tuple[int, int]:
        return (self.row1 - self.row0, self.col1 - self.col0)

    def to_dict(self) -> dict:
        return {
            "tile_id": self.tile_id,
            "row0": self.row0,
            "row1": self.row1,
            "col0": self.col0,
            "col1": self.col1,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Tile":
        return cls(int(d["tile_id"]), int(d["row0"]), int(d["row1"]),
                   int(d["col0"]), int(d["col1"]))


def _edges(length: int, step: int) -> List[Tuple[int, int]]:
    return [(s, min(s + step, length)) for s in range(0, length, step)]


class TileGrid:
    """Row-major grid of write regions over an image shape."""

    def __init__(self, image_shape: Sequence[int], tile_shape: Sequence[int]):
        h, w = int(image_shape[0]), int(image_shape[1])
        th, tw = int(tile_shape[0]), int(tile_shape[1])
        if h < 1 or w < 1:
            raise InvalidSpecError("image shape must be positive",
                                   {"image_shape": [h, w]})
        if th < 1 or tw < 1:
            raise InvalidSpecError("tile shape must be positive",
                                   {"tile_shape": [th, tw]})
        self.image_shape = (h, w)
        self.tile_shape = (th, tw)
        self._row_edges = _edges(h, th)
        self._col_edges = _edges(w, tw)
        self.tiles: List[Tile] = [
            Tile(tile_id=i * len(self._col_edges) + j, row0=r0, row1=r1, col0=c0, col1=c1)
            for i, (r0, r1) in enumerate(self._row_edges)
            for j, (c0, c1) in enumerate(self._col_edges)
        ]
        self._check_partition()

    def _check_partition(self) -> None:
        """Structural proof that write regions are a disjoint complete cover."""
        rows_ok = self._row_edges[0][0] == 0 and self._row_edges[-1][1] == self.image_shape[0]
        cols_ok = self._col_edges[0][0] == 0 and self._col_edges[-1][1] == self.image_shape[1]
        rows_ok = rows_ok and all(a[1] == b[0] for a, b in zip(self._row_edges, self._row_edges[1:]))
        cols_ok = cols_ok and all(a[1] == b[0] for a, b in zip(self._col_edges, self._col_edges[1:]))
        expected = len(self._row_edges) * len(self._col_edges)
        if not (rows_ok and cols_ok and expected == len(self.tiles)):
            raise InvalidSpecError(
                "tile grid is not a disjoint complete partition",
                {"image_shape": list(self.image_shape), "tile_shape": list(self.tile_shape)},
            )

    def __len__(self) -> int:
        return len(self.tiles)

    def __iter__(self) -> Iterator[Tile]:
        return iter(self.tiles)

    def read_window(self, tile: Tile, halo: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        """(row0, row1, col0, col1) of the halo-expanded read window; may be negative."""
        top, bottom, left, right = halo
        return (tile.row0 - top, tile.row1 + bottom,
                tile.col0 - left, tile.col1 + right)
