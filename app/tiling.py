"""Tiled reconstruction engine for large images.

Geodesic propagation can cross tile boundaries, so a single
reconstruction per tile would be wrong.  Instead this engine runs
*sweeps*: every tile is reconstructed inside a halo-extended window
(halo values read from the current global state, treated as fixed),
the tile interior is written back, and sweeps repeat until a full
sweep changes nothing.

Because the operator is monotone, inflationary and bounded above by the
mask, any fair schedule of local updates converges to the same least
fixpoint as synchronous iteration — the tests assert exact equality
with the reference kernel for several tile sizes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .kernel.queue_impl import KernelStats, reconstruct_queue
from .schemas import Connectivity

# The halo must cover the longest distance information can travel within
# one tile pass; using the tile size keeps each sweep cheap while still
# propagating across the whole tile in one go.
_MIN_TILE_SIZE = 8


@dataclass(frozen=True)
class TileJob:
    """One unit of tiled work: a tile window plus its halo window."""

    inner: tuple[slice, slice]
    outer: tuple[slice, slice]


def plan_tiles(shape: tuple[int, int], tile_size: int) -> list[TileJob]:
    """Split a 2-D shape into tile jobs with a 1-pixel-safe halo.

    The halo width equals the tile size, so a single queue pass inside
    the window can propagate information across the whole tile; whatever
    remains is carried by subsequent sweeps.
    """
    if tile_size < _MIN_TILE_SIZE:
        raise ValueError(f"tile_size must be >= {_MIN_TILE_SIZE}")
    rows, cols = shape
    jobs: list[TileJob] = []
    for r0 in range(0, rows, tile_size):
        for c0 in range(0, cols, tile_size):
            r1, c1 = min(r0 + tile_size, rows), min(c0 + tile_size, cols)
            halo = tile_size
            outer = (
                slice(max(r0 - halo, 0), min(r1 + halo, rows)),
                slice(max(c0 - halo, 0), min(c1 + halo, cols)),
            )
            jobs.append(
                TileJob(
                    inner=(slice(r0, r1), slice(c0, c1)),
                    outer=outer,
                )
            )
    return jobs


def reconstruct_tiled(
    marker: np.ndarray,
    mask: np.ndarray,
    connectivity: Connectivity = Connectivity.EIGHT,
    *,
    tile_size: int = 256,
) -> tuple[np.ndarray, KernelStats]:
    """Reconstruct by tile sweeps; returns (result, stats).

    stats.iterations counts full sweeps; stats.changed_pixels is the
    number of pixels updated in the final modifying sweep.
    """
    if marker.shape != mask.shape:
        raise ValueError("marker and mask must have the same shape")
    jobs = plan_tiles(marker.shape, tile_size)
    current = marker.copy()

    sweeps = 0
    changed_last = 0
    while True:
        sweeps += 1
        changed = 0
        for job in jobs:
            window_marker = current[job.outer]
            window_mask = mask[job.outer]
            rebuilt, _ = reconstruct_queue(
                window_marker, window_mask, connectivity
            )
            # Map the window's inner region back onto the global state.
            inner_in_outer = (
                slice(
                    job.inner[0].start - job.outer[0].start,
                    job.inner[0].stop - job.outer[0].start,
                ),
                slice(
                    job.inner[1].start - job.outer[1].start,
                    job.inner[1].stop - job.outer[1].start,
                ),
            )
            target = current[job.inner]
            update = rebuilt[inner_in_outer]
            diff = update > target
            if diff.any():
                # Only ever raise values, and never above the mask:
                # keeps the sweep schedule monotone and convergent.
                target[...] = np.maximum(target, update)
                changed += int(np.count_nonzero(diff))
        if changed == 0:
            return current, KernelStats(
                iterations=sweeps, changed_pixels=changed_last
            )
        changed_last = changed
