"""Tiled pyramid build jobs.

A build proceeds level by level.  Level 0 tiles are sliced straight from the
source (which may be a memory-mapped ``.npy`` file, so the source never has
to be resident as a whole).  Every further level is computed tile by tile:
for each output tile the job reads exactly the required window of the
previous level *through the published tile store* (dogfooding the region
reader, including its integrity checks), applies the kernel and stages the
tile.  The level becomes visible only when its manifest is atomically
published by the store.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional, Tuple

import numpy as np

from . import patterns
from .config import Settings
from .contracts import PIXEL_DTYPE, LevelMeta, RegionSpec
from .coords import auto_level_count, level_dims, tile_bounds, tile_grid
from .errors import ComputeError, InputValidationError, ResourceExhaustedError
from .kernel import KERNEL_NAMES, downsample_2x, kernel_margin
from .observability import log_event, new_run_id
from .region import read_region
from .store import TileStore


@dataclass(frozen=True)
class SourceSpec:
    """Where the level-0 pixels come from."""

    kind: str  # "synthetic" | "npy"
    pattern: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    channels: int = 1
    seed: Optional[int] = 0
    path: Optional[str] = None


@dataclass(frozen=True)
class BuildSpec:
    pyramid_id: str
    source: SourceSpec
    tile_size: int
    levels: int = 0  # 0 -> auto, down to and including 1x1
    kernel: str = "area"


@dataclass(frozen=True)
class BuildReport:
    pyramid_id: str
    run_id: str
    levels: Tuple[LevelMeta, ...]


def _check_source_pixels(width: int, height: int, channels: int,
                         settings: Settings) -> None:
    """Pixel limits count channels: memory scales with W*H*C, not W*H."""
    values = width * height * channels
    if values > settings.max_source_pixels:
        raise ResourceExhaustedError(
            f"source of {width}x{height}x{channels} values ({values}) "
            f"exceeds the limit of {settings.max_source_pixels}",
            detail={"requested_values": values,
                    "limit": settings.max_source_pixels},
        )


def _load_source(spec: SourceSpec, settings: Settings) -> np.ndarray:
    if spec.kind == "synthetic":
        if not spec.width or not spec.height:
            raise InputValidationError(
                "synthetic sources require explicit width and height"
            )
        _check_source_pixels(spec.width, spec.height, spec.channels, settings)
        return patterns.make_pattern(
            spec.pattern or "gradient",
            spec.height,
            spec.width,
            spec.channels,
            spec.seed,
        )
    if spec.kind == "npy":
        if not spec.path:
            raise InputValidationError("npy sources require a path")
        path = Path(spec.path)
        if not path.exists():
            raise InputValidationError(f"source file {path} does not exist")
        try:
            arr = np.load(path, mmap_mode="r")
        except ValueError as exc:
            raise InputValidationError(
                f"source file {path} is not a valid .npy: {exc}"
            ) from exc
        except OSError as exc:
            raise ComputeError(f"cannot read source file {path}: {exc}") from exc
        if arr.ndim == 2:
            arr = arr[..., None]
        if arr.ndim != 3 or arr.shape[2] not in (1, 3):
            raise InputValidationError(
                f"source {path} must have shape (H, W) or (H, W, 1|3), "
                f"got {arr.shape}"
            )
        _check_source_pixels(arr.shape[1], arr.shape[0], arr.shape[2], settings)
        return arr
    raise InputValidationError(
        f"unknown source kind {spec.kind!r}; expected 'synthetic' or 'npy'"
    )


def _as_pixel_array(view: np.ndarray) -> np.ndarray:
    return np.asarray(view, dtype=np.float64)


def build_pyramid(
    store: TileStore, spec: BuildSpec, settings: Settings
) -> BuildReport:
    """Build and atomically publish every level of a pyramid."""
    if spec.tile_size < 2:
        raise InputValidationError(
            f"tile_size must be >= 2, got {spec.tile_size}"
        )
    if spec.kernel not in KERNEL_NAMES:
        raise InputValidationError(
            f"unknown kernel {spec.kernel!r}; expected one of {KERNEL_NAMES}"
        )
    run_id = new_run_id()
    source = _load_source(spec.source, settings)
    src_h, src_w, channels = source.shape
    max_levels = auto_level_count(src_w, src_h)
    n_levels = spec.levels or max_levels
    if n_levels < 1:
        raise InputValidationError(f"levels must be >= 1, got {spec.levels}")
    if n_levels > max_levels:
        raise InputValidationError(
            f"levels {spec.levels} exceeds the useful maximum of "
            f"{max_levels} (down to 1x1) for a {src_w}x{src_h} source"
        )

    log_event(
        logging.INFO,
        "build_start",
        run_id,
        pyramid_id=spec.pyramid_id,
        source={"kind": spec.source.kind, "width": src_w, "height": src_h,
                "channels": channels},
        tile_size=spec.tile_size,
        kernel=spec.kernel,
        levels=n_levels,
        reason="explicit level count" if spec.levels else "auto levels to 1x1",
    )
    store.init_pyramid(
        spec.pyramid_id,
        {
            "run_id": run_id,
            "source": {
                "kind": spec.source.kind,
                "pattern": spec.source.pattern,
                "path": spec.source.path,
                "width": src_w,
                "height": src_h,
                "channels": channels,
            },
            "tile_size": spec.tile_size,
            "kernel": spec.kernel,
            "dtype": PIXEL_DTYPE,
        },
    )

    published = []
    try:
        for level in range(n_levels):
            meta = _build_level(store, spec, settings, run_id, source, level,
                                src_w, src_h, channels)
            published.append(meta)
    except Exception as exc:
        # Roll back: a failed build must not strand a half-built pyramid
        # under an id that can then never be retried (409 on re-init).
        shutil.rmtree(store.pyramid_dir(spec.pyramid_id), ignore_errors=True)
        log_event(
            logging.WARNING,
            "build_rolled_back",
            run_id,
            pyramid_id=spec.pyramid_id,
            levels_published=[m.level for m in published],
            reason=f"{type(exc).__name__}: {exc}",
        )
        raise

    log_event(
        logging.INFO,
        "build_done",
        run_id,
        pyramid_id=spec.pyramid_id,
        levels=[m.level for m in published],
        reason="all levels published",
    )
    return BuildReport(spec.pyramid_id, run_id, tuple(published))


def _build_level(
    store: TileStore,
    spec: BuildSpec,
    settings: Settings,
    run_id: str,
    source: np.ndarray,
    level: int,
    src_w: int,
    src_h: int,
    channels: int,
) -> LevelMeta:
    width, height = level_dims(src_w, src_h, level)
    tiles_x, tiles_y = tile_grid(width, height, spec.tile_size)
    meta = LevelMeta(
        level=level,
        width=width,
        height=height,
        channels=channels,
        tile_size=spec.tile_size,
        tiles_x=tiles_x,
        tiles_y=tiles_y,
        kernel=spec.kernel,
        dtype=PIXEL_DTYPE,
        run_id=run_id,
    )
    if level == 0:
        tile_iter = _level0_tiles(source, meta)
    else:
        tile_iter = _downsampled_tiles(store, spec, settings, run_id, meta)
    published = store.publish_level(spec.pyramid_id, meta, tile_iter)
    log_event(
        logging.INFO,
        "level_published",
        run_id,
        pyramid_id=spec.pyramid_id,
        level=level,
        width=width,
        height=height,
        tiles=tiles_x * tiles_y,
        reason="sliced from source" if level == 0 else "downsampled per tile",
    )
    return published


def _level0_tiles(
    source: np.ndarray, meta: LevelMeta
) -> Iterator[Tuple[int, int, np.ndarray]]:
    for ty in range(meta.tiles_y):
        for tx in range(meta.tiles_x):
            x0, y0, tw, th = tile_bounds(
                tx, ty, meta.tile_size, meta.width, meta.height
            )
            yield tx, ty, _as_pixel_array(source[y0 : y0 + th, x0 : x0 + tw, :])


def _downsampled_tiles(
    store: TileStore,
    spec: BuildSpec,
    settings: Settings,
    run_id: str,
    meta: LevelMeta,
) -> Iterator[Tuple[int, int, np.ndarray]]:
    margin = kernel_margin(spec.kernel)
    prev = store.load_manifest(spec.pyramid_id, meta.level - 1)
    ts = meta.tile_size
    for ty in range(meta.tiles_y):
        for tx in range(meta.tiles_x):
            _, _, tw, th = tile_bounds(tx, ty, ts, meta.width, meta.height)
            # Window of the previous level covering this tile's footprints.
            ix0, iy0 = 2 * tx * ts, 2 * ty * ts
            ix1 = min(2 * (tx * ts + tw), prev.width)
            iy1 = min(2 * (ty * ts + th), prev.height)
            wx0, wy0 = max(0, ix0 - margin), max(0, iy0 - margin)
            wx1, wy1 = min(prev.width, ix1 + margin), min(prev.height, iy1 + margin)
            window = read_region(
                store,
                spec.pyramid_id,
                RegionSpec(meta.level - 1, wx0, wy0, wx1 - wx0, wy1 - wy0),
                max_pixels=settings.max_region_pixels,
                run_id=run_id,
            )
            down = downsample_2x(window, spec.kernel)
            off_x = (ix0 - wx0) // 2
            off_y = (iy0 - wy0) // 2
            tile = down[off_y : off_y + th, off_x : off_x + tw, :]
            if tile.shape != (th, tw, meta.channels):
                raise ComputeError(
                    f"tiled build of level {meta.level} tile ({tx}, {ty}) "
                    f"produced {tile.shape}, expected {(th, tw, meta.channels)}"
                )
            yield tx, ty, tile
