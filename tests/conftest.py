"""Shared fixtures and *independent* reference implementations.

``naive_area_2x`` is a deliberately naive, loop-based re-implementation of
area downsampling written only for the tests — references are not generated
by the optimized kernel under test.  Even-sized cases are additionally
anchored against Pillow's BOX (area) resampler, a third-party reference.
"""

from __future__ import annotations

import numpy as np
import pytest

from pyramid_service.config import Settings
from pyramid_service.contracts import LevelMeta
from pyramid_service.coords import tile_bounds, tile_grid
from pyramid_service.store import TileStore


@pytest.fixture
def settings(tmp_path):
    return Settings(
        store_dir=tmp_path / "store",
        max_source_pixels=4_000_000,
        max_region_pixels=250_000,
    )


@pytest.fixture
def store(settings):
    return TileStore(settings.store_dir)


def naive_area_2x(a: np.ndarray) -> np.ndarray:
    """Reference area downsample: explicit per-pixel block means."""
    h, w = a.shape[:2]
    h2, w2 = (h + 1) // 2, (w + 1) // 2
    tail = a.shape[2:]
    out = np.zeros((h2, w2) + tail, dtype=np.float64)
    for y in range(h2):
        for x in range(w2):
            block = a[2 * y : min(2 * y + 2, h), 2 * x : min(2 * x + 2, w)]
            out[y, x] = block.reshape(-1, *tail).mean(axis=0)
    return out


def cascade_reference(source: np.ndarray, levels: int, kernel: str) -> list:
    """Reference pyramid by direct full-array kernel application."""
    from pyramid_service.kernel import downsample_2x

    out = [source]
    for _ in range(1, levels):
        out.append(downsample_2x(out[-1], kernel))
    return out


def make_meta(level: int, width: int, height: int, tile_size: int,
              channels: int = 1, kernel: str = "area",
              run_id: str = "testrun") -> LevelMeta:
    tiles_x, tiles_y = tile_grid(width, height, tile_size)
    return LevelMeta(
        level=level,
        width=width,
        height=height,
        channels=channels,
        tile_size=tile_size,
        tiles_x=tiles_x,
        tiles_y=tiles_y,
        kernel=kernel,
        dtype="float64",
        run_id=run_id,
    )


def constant_tiles(meta: LevelMeta, value: float = 0.0):
    """Yield ``(tx, ty, array)`` triples filled with ``value + ty*10 + tx``."""
    for ty in range(meta.tiles_y):
        for tx in range(meta.tiles_x):
            _, _, tw, th = tile_bounds(tx, ty, meta.tile_size,
                                       meta.width, meta.height)
            arr = np.full((th, tw, meta.channels),
                          value + ty * 10.0 + tx, dtype=np.float64)
            yield tx, ty, arr
