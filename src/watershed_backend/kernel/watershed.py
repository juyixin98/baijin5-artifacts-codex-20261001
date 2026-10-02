"""Deterministic immersion watershed kernel (Meyer flooding).

Algorithm assumptions (fixed by contract, not by queue accident):

* **Flooding, not distance.**  Water rises level by level from the seed
  markers through the gradient relief.  A pixel is assigned to a basin only
  when the flood front of that basin reaches it at the current water level;
  this is a genuine immersion simulation, not a nearest-seed distance map.
* **Fixed neighbourhood.**  Connectivity is 8 (default) or 4, chosen per
  request and never changed mid-run.  The neighbour offsets are enumerated
  in a fixed row-major order.
* **Deterministic order on plateaus.**  The priority queue is keyed by
  ``(elevation, insertion_counter)`` where the counter is a single
  monotonically increasing sequence.  Seeds are inserted in row-major scan
  order; neighbours of a processed pixel are inserted in the fixed offset
  order above; every pixel is enqueued at most once.  Equal-elevation
  pixels are therefore processed in a fully determined order that depends
  only on the input arrays, never on hash or heap accidents.
* **Fixed ridge definition.**  A pixel whose already-labelled neighbours
  carry two or more distinct labels becomes a *boundary* pixel
  (label ``0``).  Boundary pixels are final and never propagate, so ridge
  lines are exactly the pixels where two flood fronts meet.
* **Seedless regions.**  Active pixels the flood can never reach (their
  mask-connected component contains no seed) keep no basin label; they are
  reported as ``UNREACHED_LABEL`` (``-1``) and counted in the stats instead
  of being silently absorbed or silently dropped.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from ..errors import InvalidConnectivity, KernelInvariantError

BOUNDARY_LABEL = 0
UNREACHED_LABEL = -1

# Fixed neighbour offsets in row-major scan order.  This ordering is part of
# the determinism contract: it decides insertion order inside one water level.
NEIGHBOR_OFFSETS: dict[int, tuple[tuple[int, int], ...]] = {
    4: ((-1, 0), (0, -1), (0, 1), (1, 0)),
    8: ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)),
}

ProgressCallback = Callable[[int, int, float], None]


@dataclass(frozen=True)
class FloodStats:
    """Book-keeping that lets a caller audit what the flood did."""

    popped_pixels: int
    assigned_pixels: int
    seed_pixels: int
    boundary_pixels: int
    unreached_pixels: int
    distinct_levels: int
    min_elevation: float
    max_elevation: float
    label_counts: dict[int, int]
    connectivity: int


@dataclass(frozen=True)
class WatershedResult:
    labels: np.ndarray  # int32; >0 basin, 0 boundary, -1 unreached
    boundary: np.ndarray  # bool mask of ridge pixels (subset of labels == 0)
    stats: FloodStats


def flood_watershed(
    gradient: np.ndarray,
    markers: np.ndarray,
    connectivity: int = 8,
    mask: np.ndarray | None = None,
    progress_callback: ProgressCallback | None = None,
    progress_chunk: int = 4096,
) -> WatershedResult:
    """Run immersion watershed on a validated contract input.

    The caller (contracts layer) guarantees: ``gradient`` is 2-D finite
    float64, ``markers`` is int32 of the same shape with at least one
    positive label on an active pixel, ``mask`` (if given) is bool of the
    same shape and covers every seed.
    """
    if connectivity not in NEIGHBOR_OFFSETS:
        raise InvalidConnectivity(f"connectivity must be 4 or 8, got {connectivity}")
    offsets = NEIGHBOR_OFFSETS[connectivity]

    grad = np.asarray(gradient, dtype=np.float64)
    marks = np.asarray(markers, dtype=np.int32)
    height, width = grad.shape
    active = np.ones((height, width), dtype=bool) if mask is None else np.asarray(mask, dtype=bool)

    labels = np.where(active, marks, UNREACHED_LABEL).astype(np.int32)
    boundary = np.zeros((height, width), dtype=bool)
    queued = (marks > 0) & active

    heap: list[tuple[float, int, int, int]] = []
    counter = 0
    seed_rows, seed_cols = np.nonzero(queued)
    seed_pixels = int(len(seed_rows))
    for r, c in zip(seed_rows.tolist(), seed_cols.tolist()):
        heapq.heappush(heap, (float(grad[r, c]), counter, r, c))
        counter += 1

    total_active = int(active.sum())
    popped = 0
    assigned = 0
    boundary_count = 0
    distinct_levels = 0
    last_level: float | None = None

    while heap:
        elevation, _, r, c = heapq.heappop(heap)
        if last_level is None or elevation > last_level:
            distinct_levels += 1
            last_level = elevation
        popped += 1

        if labels[r, c] == BOUNDARY_LABEL:
            # Unlabelled pixel: decide basin or ridge from labelled neighbours.
            neighbor_labels: set[int] = set()
            for dr, dc in offsets:
                nr, nc = r + dr, c + dc
                if 0 <= nr < height and 0 <= nc < width and labels[nr, nc] > 0:
                    neighbor_labels.add(int(labels[nr, nc]))
            if len(neighbor_labels) > 1:
                # Two flood fronts meet here: fixed ridge rule.
                boundary[r, c] = True
                boundary_count += 1
                continue  # ridge pixels never propagate
            if not neighbor_labels:
                # A pixel is only enqueued by an already-labelled neighbour,
                # and labels never change once set, so this cannot happen.
                raise KernelInvariantError(
                    f"pixel ({r}, {c}) dequeued without any labelled neighbour"
                )
            labels[r, c] = neighbor_labels.pop()
            assigned += 1

        # Propagate the flood front from this labelled pixel.
        for dr, dc in offsets:
            nr, nc = r + dr, c + dc
            if (
                0 <= nr < height
                and 0 <= nc < width
                and active[nr, nc]
                and not queued[nr, nc]
            ):
                queued[nr, nc] = True
                heapq.heappush(heap, (float(grad[nr, nc]), counter, nr, nc))
                counter += 1

        if progress_callback is not None and popped % progress_chunk == 0:
            progress_callback(popped, total_active, float(elevation))

    unreached = (labels == BOUNDARY_LABEL) & ~boundary
    unreached_count = int(unreached.sum())
    if unreached_count:
        labels[unreached] = UNREACHED_LABEL

    if progress_callback is not None:
        # Always emit a terminal progress event, even for tiny images.
        progress_callback(popped, total_active, float(last_level or 0.0))

    positive = labels[labels > 0]
    label_counts = {
        int(k): int(v) for k, v in zip(*np.unique(positive, return_counts=True))
    } if positive.size else {}

    stats = FloodStats(
        popped_pixels=popped,
        assigned_pixels=assigned,
        seed_pixels=seed_pixels,
        boundary_pixels=boundary_count,
        unreached_pixels=unreached_count,
        distinct_levels=distinct_levels,
        min_elevation=float(grad[active].min()),
        max_elevation=float(grad[active].max()),
        label_counts=label_counts,
        connectivity=connectivity,
    )
    return WatershedResult(labels=labels, boundary=boundary, stats=stats)
