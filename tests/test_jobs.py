"""Tiled (chunked) job tests."""

import numpy as np
import pytest
from scipy.ndimage import shift as ndi_shift

from app.jobs import NO_VALID_TILES, run_tiled_job, tile_origins


def _big_pair(shift, size=256, seed=5):
    rng = np.random.default_rng(seed)
    canvas = rng.normal(size=(size + 64, size + 64))
    moved = ndi_shift(canvas, shift, order=3, mode="nearest")
    return canvas[32 : 32 + size, 32 : 32 + size], moved[32 : 32 + size, 32 : 32 + size]


def test_tile_origins_cover_image():
    origins = tile_origins((256, 256), 64)
    assert len(origins) == 16
    assert (0, 0) in origins and (192, 192) in origins
    assert tile_origins((32, 32), 64) == [(0, 0)]  # smaller than one tile


def test_tiled_job_recovers_global_shift(cfg):
    a, b = _big_pair((7.5, -4.5))
    result = run_tiled_job(a, b, cfg, tile_size=64, halo=16)
    assert result.status in ("ok", "uncertain")
    assert result.tiles_used >= 4
    dy, dx = result.global_shift
    assert abs(dy - 7.5) < 0.5
    assert abs(dx - (-4.5)) < 0.5
    assert result.spread_px < 1.0


def test_tiled_job_all_tiles_failed(cfg):
    a = np.full((128, 128), 100.0)
    b = np.full((128, 128), 100.0)
    result = run_tiled_job(a, b, cfg, tile_size=64, halo=16)
    assert result.status == "failed"
    assert NO_VALID_TILES in result.failures
    assert result.global_shift is None


def test_tiled_job_shape_mismatch_rejected(cfg):
    with pytest.raises(ValueError):
        run_tiled_job(np.zeros((64, 64)), np.zeros((64, 32)), cfg)
