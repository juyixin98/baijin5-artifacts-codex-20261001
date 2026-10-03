"""Tiling: per-pixel transforms must be tile-invariant (bit-identical)."""
from __future__ import annotations

import numpy as np

from colorconvert.contract import ColorMode, ImageData
from colorconvert.kernel import RenderingIntent
from colorconvert.service import ConversionRequest
from colorconvert.tiles import plan_tiles

REL = RenderingIntent.RELATIVE_COLORIMETRIC


def test_plan_tiles_covers_image_without_overlap():
    tiles = plan_tiles(200, 300, 64)
    covered = np.zeros((200, 300), dtype=int)
    for t in tiles:
        covered[t.y0 : t.y1, t.x0 : t.x1] += 1
    assert (covered == 1).all()
    assert tiles[0].index == 0 and tiles[-1].y1 == 200 and tiles[-1].x1 == 300


def test_single_tile_when_image_smaller():
    tiles = plan_tiles(10, 10, 512)
    assert len(tiles) == 1
    assert (tiles[0].y1, tiles[0].x1) == (10, 10)


def test_tiled_matches_whole_image_bit_identical(service, kernel, registry, log):
    rng = np.random.default_rng(123)
    color = rng.integers(0, 256, (200, 300, 3), dtype=np.uint8)
    image = ImageData(color=color, mode=ColorMode.RGB)

    src = registry.get("srgb", role="source")
    dst = registry.get("adobe-rgb", role="target")
    whole, _ = kernel.convert(image, src, dst, REL, False)

    req = ConversionRequest(
        image=image,
        source_profile="srgb",
        target_profile="adobe-rgb",
        tile_size=64,
    )
    outcome = service.convert(req, log)
    assert outcome.job.status.value == "completed"
    assert np.array_equal(outcome.result.color, whole.color)
    assert outcome.job.report["tiles"] == len(plan_tiles(200, 300, 64))


def test_tiled_with_alpha_bit_identical(service, kernel, registry, log):
    rng = np.random.default_rng(9)
    color = rng.integers(0, 256, (100, 100, 3), dtype=np.uint8)
    alpha = rng.integers(0, 256, (100, 100), dtype=np.uint8)
    image = ImageData(color=color, mode=ColorMode.RGB, alpha=alpha)

    src = registry.get("srgb", role="source")
    dst = registry.get("prophoto-rgb", role="target")
    whole, _ = kernel.convert(image, src, dst, REL, False)

    req = ConversionRequest(
        image=image,
        source_profile="srgb",
        target_profile="prophoto-rgb",
        tile_size=32,
    )
    outcome = service.convert(req, log)
    assert np.array_equal(outcome.result.color, whole.color)
    assert np.array_equal(outcome.result.alpha, alpha)
