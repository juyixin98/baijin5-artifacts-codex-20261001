"""分块构建作业：逐层、逐瓦片构建多分辨率金字塔。

- 第 0 层：按瓦片窗口调用合成生成器（坐标函数，窗口结果与整图一致）。
- 第 L 层：对每个输出瓦片，从已发布的 L-1 层读取精确对应的源窗口
  [2*y0 : min(2*y1, H), 2*x0 : min(2*x1, W))，面积降采样后恰好得到该瓦片，
  无需额外 halo（box 核支持域与 2 倍网格对齐）。
- 每层通过 TileStore.publish_level 原子发布；image.json 最后落盘。
"""
from __future__ import annotations

import logging
from typing import Iterator

import numpy as np

from . import synth
from .contracts import GeneratorSpec, ImageMeta, LevelMeta
from .errors import ComputeError, InputError
from .kernel import area_downsample2, level_shape, max_levels_for
from .logging_utils import log_event, new_run_id
from .tilestore import TileStore


def _level_meta(image_w: int, image_h: int, level: int, tile_size: int) -> LevelMeta:
    h, w = level_shape(image_h, image_w, level)
    return LevelMeta(
        level=level,
        width=w,
        height=h,
        tile_size=tile_size,
        tiles_x=(w + tile_size - 1) // tile_size,
        tiles_y=(h + tile_size - 1) // tile_size,
    )


def _level0_tiles(
    spec: GeneratorSpec, lm: LevelMeta
) -> Iterator[tuple[int, int, np.ndarray]]:
    for ty in range(lm.tiles_y):
        for tx in range(lm.tiles_x):
            y0, x0 = ty * lm.tile_size, tx * lm.tile_size
            th = min(lm.tile_size, lm.height - y0)
            tw = min(lm.tile_size, lm.width - x0)
            yield ty, tx, synth.generate_window(spec, y0, x0, th, tw)


def _downsampled_tiles(
    store: TileStore, image_id: str, lm: LevelMeta, prev: LevelMeta
) -> Iterator[tuple[int, int, np.ndarray]]:
    for ty in range(lm.tiles_y):
        for tx in range(lm.tiles_x):
            y0, x0 = ty * lm.tile_size, tx * lm.tile_size
            th = min(lm.tile_size, lm.height - y0)
            tw = min(lm.tile_size, lm.width - x0)
            src = store.read_region(
                image_id,
                prev.level,
                x=2 * x0,
                y=2 * y0,
                w=min(2 * tw, prev.width - 2 * x0),
                h=min(2 * th, prev.height - 2 * y0),
            )
            tile = area_downsample2(src)
            if tile.shape != (th, tw):
                raise ComputeError(  # 不应发生；防御性检查
                    f"downsampled tile shape {tile.shape} != expected {(th, tw)}"
                )
            yield ty, tx, tile


def build_pyramid(
    store: TileStore,
    image_id: str,
    spec: GeneratorSpec,
    *,
    n_levels: int,
    tile_size: int,
    logger: logging.Logger,
    run_id: str | None = None,
) -> ImageMeta:
    """构建并发布 n_levels 层金字塔，返回图像级元数据。"""
    run_id = run_id or new_run_id()
    if n_levels < 1:
        raise InputError(f"n_levels must be >= 1, got {n_levels}")
    max_lv = max_levels_for(spec.height, spec.width)
    if n_levels > max_lv:
        raise InputError(
            f"n_levels={n_levels} exceeds useful maximum {max_lv} "
            f"for {spec.width}x{spec.height} (already 1x1)"
        )
    log_event(
        logger,
        "build_start",
        run_id=run_id,
        image_id=image_id,
        generator=spec.kind,
        width=spec.width,
        height=spec.height,
        n_levels=n_levels,
        tile_size=tile_size,
    )
    level_metas: list[LevelMeta] = []
    for level in range(n_levels):
        lm = _level_meta(spec.width, spec.height, level, tile_size)
        log_event(
            logger,
            "level_build_start",
            run_id=run_id,
            image_id=image_id,
            level=level,
            width=lm.width,
            height=lm.height,
            tiles=lm.tiles_x * lm.tiles_y,
        )
        if level == 0:
            tiles = _level0_tiles(spec, lm)
        else:
            tiles = _downsampled_tiles(store, image_id, lm, level_metas[-1])
        store.publish_level(image_id, lm, tiles)
        log_event(
            logger,
            "level_published",
            run_id=run_id,
            image_id=image_id,
            level=level,
            width=lm.width,
            height=lm.height,
            tiles=lm.tiles_x * lm.tiles_y,
        )
        level_metas.append(lm)
    meta = ImageMeta(
        image_id=image_id,
        width=spec.width,
        height=spec.height,
        tile_size=tile_size,
        levels=tuple(level_metas),
    )
    store.write_image_meta(image_id, meta.to_dict())
    log_event(
        logger,
        "image_published",
        run_id=run_id,
        image_id=image_id,
        levels=n_levels,
        reason="all levels atomically published; image.json is the final commit point",
    )
    return meta
