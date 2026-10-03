"""Data contracts shared across the pyramid service.

Pixel contract
--------------
* Pixel arrays are ``float64`` with shape ``(H, W, C)`` (C == 1 or 3) and
  values nominally in ``[0.0, 1.0]``.
* Level 0 is the source image.  Level ``L`` has scale ``2**L`` and dimensions
  ``ceil(W / 2**L) x ceil(H / 2**L)`` — odd sizes never drop a row or column.
* Fixed pixel-center mapping: the center of level-``L`` pixel ``(x, y)`` sits
  at level-0 coordinates ``((x + 0.5) * 2**L - 0.5, (y + 0.5) * 2**L - 0.5)``.
  For odd source sizes the last center of a level may extend past the last
  source pixel center by less than half a level pixel; the kernels renormalize
  edge coverage accordingly.

Tile contract
-------------
* A level is a grid of tiles of ``tile_size`` pixels; edge tiles are clipped,
  never padded.
* Tiles are stored as ``.npy`` files plus a per-level ``manifest.json``
  carrying shape, dtype and sha256 of every tile.  A level is visible to
  readers only after its manifest has been atomically published.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import re

from .errors import InputValidationError

PIXEL_DTYPE = "float64"
ALLOWED_CHANNELS = (1, 3)

# Pyramid ids become directory names; restrict them so a crafted id can never
# escape the store root (path traversal) or collide with staging dirs.
PYRAMID_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_pyramid_id(pyramid_id: str) -> str:
    if not PYRAMID_ID_RE.match(pyramid_id or "") or ".." in pyramid_id:
        raise InputValidationError(
            f"invalid pyramid id {pyramid_id!r}: must match "
            f"{PYRAMID_ID_RE.pattern} and contain no '..'"
        )
    return pyramid_id


@dataclass(frozen=True)
class RegionSpec:
    """A rectangular region request in level pixel coordinates."""

    level: int
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class TileRecord:
    """Manifest entry for one stored tile."""

    tx: int
    ty: int
    x0: int
    y0: int
    width: int
    height: int
    path: str  # relative to the level directory
    sha256: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tx": self.tx,
            "ty": self.ty,
            "x0": self.x0,
            "y0": self.y0,
            "width": self.width,
            "height": self.height,
            "path": self.path,
            "sha256": self.sha256,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "TileRecord":
        return TileRecord(
            tx=int(d["tx"]),
            ty=int(d["ty"]),
            x0=int(d["x0"]),
            y0=int(d["y0"]),
            width=int(d["width"]),
            height=int(d["height"]),
            path=str(d["path"]),
            sha256=str(d["sha256"]),
        )


@dataclass(frozen=True)
class LevelMeta:
    """Published metadata for one pyramid level."""

    level: int
    width: int
    height: int
    channels: int
    tile_size: int
    tiles_x: int
    tiles_y: int
    kernel: str
    dtype: str
    run_id: str
    tiles: Tuple[TileRecord, ...] = field(default_factory=tuple)

    def tile_record(self, tx: int, ty: int) -> TileRecord:
        for rec in self.tiles:
            if rec.tx == tx and rec.ty == ty:
                return rec
        raise KeyError(f"no tile record for ({tx}, {ty}) in level {self.level}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "width": self.width,
            "height": self.height,
            "channels": self.channels,
            "tile_size": self.tile_size,
            "tiles_x": self.tiles_x,
            "tiles_y": self.tiles_y,
            "kernel": self.kernel,
            "dtype": self.dtype,
            "run_id": self.run_id,
            "tiles": [t.to_dict() for t in self.tiles],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "LevelMeta":
        return LevelMeta(
            level=int(d["level"]),
            width=int(d["width"]),
            height=int(d["height"]),
            channels=int(d["channels"]),
            tile_size=int(d["tile_size"]),
            tiles_x=int(d["tiles_x"]),
            tiles_y=int(d["tiles_y"]),
            kernel=str(d["kernel"]),
            dtype=str(d["dtype"]),
            run_id=str(d["run_id"]),
            tiles=tuple(TileRecord.from_dict(t) for t in d.get("tiles", [])),
        )
