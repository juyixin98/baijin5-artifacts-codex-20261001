"""Chunked (tiled) estimation jobs.

Large pairs are processed as a grid of overlapping tiles; each tile gets
an independent kernel estimate, and per-tile results are aggregated with
a confidence-weighted median. Tiles that fail are excluded and listed —
aggregation never hides them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import KernelConfig
from app.kernel.pipeline import EstimateResult, estimate_shift

NO_VALID_TILES = "NO_VALID_TILES"
TILE_DISAGREEMENT = "TILE_DISAGREEMENT"
FEW_VALID_TILES = "FEW_VALID_TILES"

MAX_SPREAD_PX = 1.0  # weighted std above this -> TILE_DISAGREEMENT


@dataclass
class TileResult:
    origin: tuple[int, int]
    estimate: EstimateResult


@dataclass
class TiledJobResult:
    status: str  # "ok" | "uncertain" | "failed"
    global_shift: tuple[float, float] | None
    spread_px: float | None
    tiles: list[TileResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    @property
    def tiles_used(self) -> int:
        return sum(1 for t in self.tiles if t.estimate.status == "ok")


def tile_origins(
    shape: tuple[int, int], tile_size: int
) -> list[tuple[int, int]]:
    h, w = shape
    if h < tile_size or w < tile_size:
        return [(0, 0)]
    ys = list(range(0, h - tile_size + 1, tile_size))
    xs = list(range(0, w - tile_size + 1, tile_size))
    return [(y, x) for y in ys for x in xs]


def _crop_with_halo(
    img: np.ndarray, y: int, x: int, tile_size: int, halo: int
) -> np.ndarray:
    h, w = img.shape
    y0, x0 = max(0, y - halo), max(0, x - halo)
    y1, x1 = min(h, y + tile_size + halo), min(w, x + tile_size + halo)
    return img[y0:y1, x0:x1]


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cutoff = 0.5 * w.sum()
    return float(v[np.searchsorted(np.cumsum(w), cutoff)])


def run_tiled_job(
    img1: np.ndarray,
    img2: np.ndarray,
    cfg: KernelConfig,
    tile_size: int = 64,
    halo: int = 16,
) -> TiledJobResult:
    if img1.shape != img2.shape:
        raise ValueError("tiled job requires a same-shape pair")

    tiles: list[TileResult] = []
    for y, x in tile_origins(img1.shape, tile_size):
        crop_a = _crop_with_halo(img1, y, x, tile_size, halo)
        crop_b = _crop_with_halo(img2, y, x, tile_size, halo)
        tiles.append(TileResult(origin=(y, x), estimate=estimate_shift(crop_a, crop_b, cfg)))

    usable = [t for t in tiles if t.estimate.status == "ok" and t.estimate.shift]
    failures: list[str] = []
    uncertainties: list[str] = []

    if not usable:
        return TiledJobResult(
            status="failed",
            global_shift=None,
            spread_px=None,
            tiles=tiles,
            failures=[NO_VALID_TILES],
            uncertainties=uncertainties,
        )

    dys = np.array([t.estimate.shift[0] for t in usable])
    dxs = np.array([t.estimate.shift[1] for t in usable])
    weights = np.array([max(t.estimate.confidence, 1e-6) for t in usable])
    global_shift = (_weighted_median(dys, weights), _weighted_median(dxs, weights))
    spread = float(
        max(
            np.sqrt(np.average((dys - global_shift[0]) ** 2, weights=weights)),
            np.sqrt(np.average((dxs - global_shift[1]) ** 2, weights=weights)),
        )
    )

    if spread > MAX_SPREAD_PX:
        uncertainties.append(TILE_DISAGREEMENT)
    if len(usable) * 2 < len(tiles):
        uncertainties.append(FEW_VALID_TILES)

    status = "uncertain" if uncertainties else "ok"
    return TiledJobResult(
        status=status,
        global_shift=global_shift,
        spread_px=round(spread, 4),
        tiles=tiles,
        failures=failures,
        uncertainties=uncertainties,
    )
