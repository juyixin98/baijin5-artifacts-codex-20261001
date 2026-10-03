"""Chunked (tiled) job execution.

Large images are processed tile by tile. Each tile is reconstructed with the
FIFO queue kernel on a 1-pixel read-only halo copied from the current global
state; only the tile interior is written back. Sweeps repeat until a full
sweep changes nothing, which is exactly the global fixed point — values only
increase and are bounded by the mask, so termination is guaranteed.

The number of sweeps and the per-sweep change counts are recorded in the
trace, so convergence is observable rather than asserted visually.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np

from .errors import FixedPointNotReached
from .kernel import reconstruct_queue


@dataclass(frozen=True)
class TiledTrace:
    algorithm: str = "tiled"
    tile_shape: tuple[int, int] = (0, 0)
    sweeps: int = 0
    changed_per_sweep: tuple[int, ...] = ()


def tile_slices(
    shape: tuple[int, int], tile_shape: tuple[int, int]
) -> Iterator[tuple[slice, slice]]:
    h, w = shape
    th, tw = tile_shape
    for y0 in range(0, h, th):
        for x0 in range(0, w, tw):
            yield slice(y0, min(y0 + th, h)), slice(x0, min(x0 + tw, w))


def reconstruct_tiled(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    tile_shape: tuple[int, int] = (64, 64),
    connectivity: int = 4,
) -> tuple[np.ndarray, TiledTrace]:
    th, tw = tile_shape
    if th < 1 or tw < 1:
        raise ValueError("tile dimensions must be >= 1")

    rec = np.asarray(marker, dtype=np.float64).copy()
    mask = np.asarray(mask, dtype=np.float64)
    h, w = rec.shape
    safeguard = h * w + 1
    changed_per_sweep: list[int] = []

    for _ in range(safeguard):
        changed = 0
        for ys, xs in tile_slices(rec.shape, tile_shape):
            # 1-pixel halo, clipped at image borders (edge-ignore rule).
            hy = slice(max(ys.start - 1, 0), min(ys.stop + 1, h))
            hx = slice(max(xs.start - 1, 0), min(xs.stop + 1, w))
            sub, _ = reconstruct_queue(rec[hy, hx], mask[hy, hx], connectivity)
            iy = slice(ys.start - hy.start, ys.stop - hy.start)
            ix = slice(xs.start - hx.start, xs.stop - hx.start)
            interior = sub[iy, ix]
            region = rec[ys, xs]
            changed += int(np.count_nonzero(interior != region))
            rec[ys, xs] = interior
        changed_per_sweep.append(changed)
        if changed == 0:
            return rec, TiledTrace(
                tile_shape=(th, tw),
                sweeps=len(changed_per_sweep),
                changed_per_sweep=tuple(changed_per_sweep),
            )
    raise FixedPointNotReached(f"tiled kernel did not converge in {safeguard} sweeps")
