"""Region queries: stitch an arbitrary rectangle across tile boundaries.

The reader validates the request against the published level metadata,
loads exactly the tiles the rectangle touches (each integrity-checked by
the store) and pastes them into one array.  Rectangles may extend past the
level bounds — they are clipped, and the clipped shape is what the caller
gets back.  Rectangles fully outside the level, non-positive sizes or
negative levels are input errors; oversized rectangles are rejected with a
resource-exhaustion error before any tile is read.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from .contracts import RegionSpec
from .errors import InputValidationError, ResourceExhaustedError
from .observability import log_event, new_run_id
from .store import TileStore


def read_region(
    store: TileStore,
    pyramid_id: str,
    spec: RegionSpec,
    *,
    max_pixels: Optional[int] = None,
    run_id: Optional[str] = None,
) -> np.ndarray:
    """Return the ``(h, w, C)`` float64 array for ``spec`` (clipped to bounds)."""
    run_id = run_id or new_run_id()
    if spec.level < 0:
        raise InputValidationError(f"level must be >= 0, got {spec.level}")
    if spec.w <= 0 or spec.h <= 0:
        raise InputValidationError(
            f"region size must be positive, got {spec.w}x{spec.h}"
        )
    if spec.x < 0 or spec.y < 0:
        raise InputValidationError(
            f"region origin must be >= 0, got ({spec.x}, {spec.y})"
        )

    meta = store.load_manifest(pyramid_id, spec.level)  # NotFoundError if absent
    if spec.x >= meta.width or spec.y >= meta.height:
        raise InputValidationError(
            f"region origin ({spec.x}, {spec.y}) is outside level "
            f"{spec.level} bounds {meta.width}x{meta.height}"
        )

    x1 = min(spec.x + spec.w, meta.width)
    y1 = min(spec.y + spec.h, meta.height)
    out_w, out_h = x1 - spec.x, y1 - spec.y
    clipped = (out_w, out_h) != (spec.w, spec.h)
    # The limit counts channel values: memory scales with W*H*C, not W*H.
    values = out_w * out_h * meta.channels
    if max_pixels is not None and values > max_pixels:
        raise ResourceExhaustedError(
            f"region of {out_w}x{out_h}x{meta.channels} values ({values}) "
            f"exceeds the limit of {max_pixels}",
            detail={"requested_values": values, "limit": max_pixels},
        )

    ts = meta.tile_size
    tx0, ty0 = spec.x // ts, spec.y // ts
    tx1, ty1 = (x1 - 1) // ts, (y1 - 1) // ts
    out = np.empty((out_h, out_w, meta.channels), dtype=np.float64)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            tile = store.read_tile(pyramid_id, spec.level, tx, ty)
            # Intersection of the tile's pixel span with the requested rect.
            ix0 = max(spec.x, tx * ts)
            iy0 = max(spec.y, ty * ts)
            ix1 = min(x1, tx * ts + tile.shape[1])
            iy1 = min(y1, ty * ts + tile.shape[0])
            out[
                iy0 - spec.y : iy1 - spec.y,
                ix0 - spec.x : ix1 - spec.x,
                :,
            ] = tile[
                iy0 - ty * ts : iy1 - ty * ts,
                ix0 - tx * ts : ix1 - tx * ts,
                :,
            ]

    log_event(
        logging.INFO,
        "region_read",
        run_id,
        pyramid_id=pyramid_id,
        level=spec.level,
        request={"x": spec.x, "y": spec.y, "w": spec.w, "h": spec.h},
        delivered={"w": out_w, "h": out_h},
        clipped_to_bounds=clipped,
        tiles_used=(tx1 - tx0 + 1) * (ty1 - ty0 + 1),
        reason="region clipped to level bounds" if clipped else "exact region",
    )
    return out
