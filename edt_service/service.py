"""Service orchestration: contract in, provenance-rich result out.

Decides direct vs. tiled execution, applies the independently-defined
degenerate-case semantics (no source / all sources), and assembles the
result envelope with versions, timings and tile provenance.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import __version__
from .config import Settings
from .kernel import INF, NO_SOURCE, edt2d
from .logging_setup import get_logger
from .tiling import TileReport, edt2d_tiled

logger = get_logger()


@dataclass
class EdtResult:
    dist: np.ndarray
    labels: np.ndarray
    mode: str  # "direct" | "tiled" | "degenerate-empty" | "degenerate-full"
    tile_reports: list[TileReport] = field(default_factory=list)
    elapsed_ms: float = 0.0
    versions: dict = field(default_factory=dict)


def _versions() -> dict:
    import fastapi
    import pydantic
    import scipy
    import PIL

    return {
        "edt_service": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pillow": PIL.__version__,
        "fastapi": fastapi.__version__,
        "pydantic": pydantic.__version__,
    }


def compute_edt(
    mask: np.ndarray,
    spacing: tuple[float, float],
    settings: Settings,
    tile_size: Optional[int] = None,
) -> EdtResult:
    """Run the exact EDT, choosing the execution strategy.

    Degenerate rasters are defined independently of the kernel and
    short-circuited here:
    - no source pixel  -> all distances +inf, all labels -1;
    - all source pixels -> all distances 0, each label its own flat index.
    """
    started = time.perf_counter()
    height, width = mask.shape
    n_pixels = height * width
    n_sources = int(mask.sum())
    logger.info(
        "compute_edt start: shape=%sx%s spacing=%s sources=%s/%s",
        height,
        width,
        spacing,
        n_sources,
        n_pixels,
    )

    if n_sources == 0:
        dist = np.full((height, width), INF)
        labels = np.full((height, width), NO_SOURCE, dtype=np.int64)
        mode = "degenerate-empty"
        reports: list[TileReport] = []
    elif n_sources == n_pixels:
        dist = np.zeros((height, width))
        labels = np.arange(n_pixels, dtype=np.int64).reshape(height, width)
        mode = "degenerate-full"
        reports = []
    else:
        effective_tile = tile_size or settings.tile_size
        if tile_size is None and n_pixels <= settings.direct_max_pixels:
            dist, labels = edt2d(mask, spacing)
            mode = "direct"
            reports = []
        else:
            dist, labels, reports = edt2d_tiled(mask, spacing, effective_tile)
            mode = "tiled"

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "compute_edt done: mode=%s tiles=%s elapsed_ms=%.2f",
        mode,
        len(reports),
        elapsed_ms,
    )
    return EdtResult(
        dist=dist,
        labels=labels,
        mode=mode,
        tile_reports=reports,
        elapsed_ms=elapsed_ms,
        versions=_versions(),
    )
