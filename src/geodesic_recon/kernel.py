"""Numerical kernels for geodesic reconstruction by dilation.

Reconstruction of ``marker`` under ``mask`` is the iteration to a fixed point of

    rec_{n+1} = min(dilate(rec_n), mask),   rec_0 = marker   (marker <= mask)

Two equivalent implementations are provided:

* :func:`reconstruct_sync`  -- whole-image synchronous iteration (reference semantics)
* :func:`reconstruct_queue` -- FIFO queue propagation (Vincent-style); visits only
  pixels that can still grow, but reaches the same fixed point.

Boundary rule (fixed): out-of-bounds neighbors are ignored ("edge-ignore");
no wrap-around, no replicated padding. Both kernels record convergence
information in a trace object instead of only returning pixels.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .errors import FixedPointNotReached

#: Fixed neighborhood definitions (dy, dx).
NEIGHBOR_OFFSETS: dict[int, tuple[tuple[int, int], ...]] = {
    4: ((-1, 0), (1, 0), (0, -1), (0, 1)),
    8: (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1), (0, 1),
        (1, -1), (1, 0), (1, 1),
    ),
}


@dataclass(frozen=True)
class SyncTrace:
    algorithm: str = "sync"
    iterations: int = 0  # dilation passes executed, including the final stable pass
    changed_per_iteration: tuple[int, ...] = ()


@dataclass(frozen=True)
class QueueTrace:
    algorithm: str = "queue"
    initial_queue_size: int = 0
    pushes: int = 0
    pops: int = 0


def offsets_for(connectivity: int) -> tuple[tuple[int, int], ...]:
    try:
        return NEIGHBOR_OFFSETS[connectivity]
    except KeyError:
        raise ValueError(f"connectivity must be 4 or 8, got {connectivity}") from None


def _neighbor_view(arr: np.ndarray, dy: int, dx: int, fill: float) -> np.ndarray:
    """out[y, x] = arr[y+dy, x+dx] where in bounds, else ``fill``."""
    h, w = arr.shape
    out = np.full(arr.shape, fill, dtype=arr.dtype)
    dy0, dy1 = max(0, -dy), min(h, h - dy)
    dx0, dx1 = max(0, -dx), min(w, w - dx)
    sy0, sy1 = max(0, dy), min(h, h + dy)
    sx0, sx1 = max(0, dx), min(w, w + dx)
    out[dy0:dy1, dx0:dx1] = arr[sy0:sy1, sx0:sx1]
    return out


def dilate_once(values: np.ndarray, connectivity: int = 4) -> np.ndarray:
    """Flat dilation with the fixed edge-ignore boundary rule."""
    offsets = offsets_for(connectivity)
    out = values.copy()
    for dy, dx in offsets:
        np.maximum(out, _neighbor_view(values, dy, dx, -np.inf), out=out)
    return out


def reconstruct_sync(
    marker: np.ndarray, mask: np.ndarray, connectivity: int = 4
) -> tuple[np.ndarray, SyncTrace]:
    """Synchronous iteration to the fixed point. Reference semantics."""
    rec = np.asarray(marker, dtype=np.float64).copy()
    mask = np.asarray(mask, dtype=np.float64)
    safeguard = rec.size + 1  # geodesic distance never exceeds pixel count
    changed_hist: list[int] = []
    for _ in range(safeguard):
        new = np.minimum(dilate_once(rec, connectivity), mask)
        changed = int(np.count_nonzero(new != rec))
        changed_hist.append(changed)
        rec = new
        if changed == 0:
            return rec, SyncTrace(iterations=len(changed_hist), changed_per_iteration=tuple(changed_hist))
    raise FixedPointNotReached(f"sync kernel did not converge in {safeguard} passes")


def reconstruct_queue(
    marker: np.ndarray, mask: np.ndarray, connectivity: int = 4
) -> tuple[np.ndarray, QueueTrace]:
    """FIFO queue propagation. Result is identical to :func:`reconstruct_sync`."""
    rec = np.asarray(marker, dtype=np.float64).copy()
    mask = np.asarray(mask, dtype=np.float64)
    offsets = offsets_for(connectivity)
    h, w = rec.shape

    # Seed the queue with every pixel that can raise at least one neighbor.
    needs = np.zeros(rec.shape, dtype=bool)
    for dy, dx in offsets:
        q_rec = _neighbor_view(rec, dy, dx, np.inf)
        q_mask = _neighbor_view(mask, dy, dx, np.inf)
        needs |= q_rec < np.minimum(rec, q_mask)
    queue: deque[tuple[int, int]] = deque(map(tuple, np.argwhere(needs)))
    initial_size = len(queue)
    pushes = initial_size
    pops = 0

    while queue:
        y, x = queue.popleft()
        pops += 1
        v = rec[y, x]
        for dy, dx in offsets:
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w:
                if rec[ny, nx] < v and rec[ny, nx] < mask[ny, nx]:
                    rec[ny, nx] = min(v, mask[ny, nx])
                    queue.append((ny, nx))
                    pushes += 1

    return rec, QueueTrace(initial_queue_size=initial_size, pushes=pushes, pops=pops)


def reconstruct(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    algorithm: Literal["sync", "queue", "tiled"] = "queue",
    connectivity: int = 4,
    tile_shape: tuple[int, int] = (64, 64),
):
    """Dispatch to the requested kernel. Returns (result, trace)."""
    if algorithm == "sync":
        return reconstruct_sync(marker, mask, connectivity)
    if algorithm == "queue":
        return reconstruct_queue(marker, mask, connectivity)
    if algorithm == "tiled":
        from .tiles import reconstruct_tiled

        return reconstruct_tiled(marker, mask, tile_shape=tile_shape, connectivity=connectivity)
    raise ValueError(f"unknown algorithm {algorithm!r}")
