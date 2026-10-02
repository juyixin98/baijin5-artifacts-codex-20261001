"""Numerical kernel: marker-based flooding watershed.

Algorithm (Meyer-style priority flood, deterministic variant):

* Seeds (marker pixels with label > 0) are pushed into a global min-heap
  keyed by ``(elevation, flat_index)``. Because the flat row-major index is
  part of the key, the processing order of pixels on the same elevation
  plateau is fully determined by the input arrays — never by queue
  accidents, hash seeds, or thread scheduling.
* Popping the lowest pixel ``p`` assigns its label to every still-unlabelled
  in-mask neighbour ``q``; ``q`` is pushed with its own elevation.
* If a popped pixel ``p`` sees an in-mask neighbour already carrying a
  *different* basin label, ``p`` lies on the ridge between two basins and is
  reclassified as a watershed-line pixel (label ``WATERSHED_LINE``), unless
  ``p`` is itself a seed: seeds are never demoted, so conflicting adjacent
  seeds keep their labels and the conflict is reported in the stats.
* Pixels outside the optional mask are never visited and remain
  ``UNLABELED`` — this is the explicit handling of seedless / excluded
  regions.

The output is therefore a genuine water-level propagation (basins plus
ridge lines), not a nearest-seed distance assignment: a pixel reachable
only over a high pass is flooded later than a pixel reachable through a low
corridor, regardless of Euclidean distance to any seed.

Chunking: the heap is drained in batches of ``chunk_size`` pops. Chunking
affects only progress reporting; the pop sequence (and hence the result) is
identical for any chunk size.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from app.core.connectivity import iter_neighbors, neighbor_offsets  # noqa: F401  (re-export)

WATERSHED_LINE: int = -1
UNLABELED: int = 0

# Human-readable statement of the tie-break rule, embedded in run logs so the
# decision basis is visible next to the numbers.
TIE_BREAK_RULE = (
    "equal-elevation pixels are popped in ascending row-major flat index; "
    "a pixel adjacent to a different basin becomes a watershed-line pixel "
    "unless it is a seed"
)


class WatershedInputError(ValueError):
    """Raised when kernel inputs violate the numerical contract.

    Carries a stable ``category`` so the API layer can map it to an error
    response instead of a generic 500.
    """

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class WatershedStats:
    pixels_total: int
    pixels_in_mask: int
    pixels_labeled: int
    pixels_watershed: int
    pixels_unlabeled: int
    seed_count: int
    label_count: int
    conflict_count: int
    seed_conflict_count: int
    chunks_processed: int
    tie_break_rule: str = field(default=TIE_BREAK_RULE)

    def as_dict(self) -> dict:
        return {
            "pixels_total": self.pixels_total,
            "pixels_in_mask": self.pixels_in_mask,
            "pixels_labeled": self.pixels_labeled,
            "pixels_watershed": self.pixels_watershed,
            "pixels_unlabeled": self.pixels_unlabeled,
            "seed_count": self.seed_count,
            "label_count": self.label_count,
            "conflict_count": self.conflict_count,
            "seed_conflict_count": self.seed_conflict_count,
            "chunks_processed": self.chunks_processed,
            "tie_break_rule": self.tie_break_rule,
        }


@dataclass(frozen=True)
class WatershedResult:
    labels: np.ndarray  # int32, same shape as input; WATERSHED_LINE / UNLABELED sentinels
    boundary: np.ndarray  # bool, True exactly where labels == WATERSHED_LINE
    stats: WatershedStats


def validate_kernel_inputs(
    elevation: np.ndarray,
    markers: np.ndarray,
    connectivity: int,
    mask: Optional[np.ndarray],
) -> None:
    """Fail fast with a categorised error on any contract violation."""
    if elevation.ndim != 2:
        raise WatershedInputError("not_2d", f"elevation must be 2-D, got shape {elevation.shape}")
    if markers.shape != elevation.shape:
        raise WatershedInputError(
            "shape_mismatch",
            f"markers shape {markers.shape} != elevation shape {elevation.shape}",
        )
    if not np.issubdtype(markers.dtype, np.integer):
        raise WatershedInputError("markers_not_integer", f"markers dtype must be integer, got {markers.dtype}")
    if markers.size == 0:
        raise WatershedInputError("empty_input", "elevation/markers must be non-empty")
    if not np.all(np.isfinite(elevation)):
        raise WatershedInputError("non_finite_elevation", "elevation contains NaN or inf")
    if int(markers.min()) < 0:
        raise WatershedInputError("negative_marker", "marker labels must be >= 0 (0 = unmarked)")
    try:
        neighbor_offsets(connectivity)
    except ValueError as exc:
        raise WatershedInputError("bad_connectivity", str(exc)) from exc
    if mask is not None:
        if mask.shape != elevation.shape:
            raise WatershedInputError(
                "shape_mismatch", f"mask shape {mask.shape} != elevation shape {elevation.shape}"
            )
        seed_mask = markers > 0
        if np.any(seed_mask & ~mask):
            raise WatershedInputError(
                "seed_outside_mask", "at least one seed pixel lies outside the mask"
            )
    if not np.any(markers > 0):
        raise WatershedInputError("no_seeds", "markers contain no seed (no label > 0)")


def flood_watershed(
    elevation: np.ndarray,
    markers: np.ndarray,
    *,
    connectivity: int = 8,
    mask: Optional[np.ndarray] = None,
    chunk_size: Optional[int] = None,
    on_progress: Optional[Callable[[int, int, int, float], None]] = None,
) -> WatershedResult:
    """Run the deterministic flooding watershed.

    Args:
        elevation: 2-D array of flood levels (e.g. a gradient magnitude).
        markers: 2-D integer array; ``> 0`` are seed labels, ``0`` unmarked.
        connectivity: 4 or 8 (fixed neighbourhood tables, see connectivity.py).
        mask: optional 2-D boolean array; ``False`` pixels stay UNLABELED.
        chunk_size: pops per progress batch; ``None`` processes in one batch.
        on_progress: optional callback ``(chunk_index, processed, total, level)``.

    Returns:
        WatershedResult with basin labels, ridge-line mask and run statistics.

    Raises:
        WatershedInputError: on any input contract violation.
    """
    elev = np.asarray(elevation, dtype=np.float64)
    marks = np.asarray(markers)
    validate_kernel_inputs(elev, marks, connectivity, mask)

    n_rows, n_cols = elev.shape
    if mask is None:
        in_mask = np.ones((n_rows, n_cols), dtype=bool)
    else:
        in_mask = np.asarray(mask, dtype=bool)

    labels = np.where(in_mask, marks, UNLABELED).astype(np.int32)
    seed_pixel = marks > 0

    # Global deterministic heap: (elevation, flat_index). The flat index
    # tie-break is what makes plateau processing order a function of the
    # input alone.
    heap: list[tuple[float, int]] = [
        (float(elev.flat[i]), i) for i in np.flatnonzero(seed_pixel & in_mask)
    ]
    heapq.heapify(heap)

    total = int(in_mask.sum())
    processed = 0
    chunk_index = 0
    conflict_count = 0
    seed_conflict_count = 0
    effective_chunk = chunk_size if chunk_size and chunk_size > 0 else max(total, 1)

    while heap:
        # Drain up to ``effective_chunk`` items, then report progress. The
        # pop sequence itself is independent of the batch boundary.
        batch = min(effective_chunk, len(heap))
        for _ in range(batch):
            level, idx = heapq.heappop(heap)
            r, c = divmod(idx, n_cols)
            p_label = int(labels[r, c])
            if p_label == WATERSHED_LINE:
                continue
            for qr, qc in iter_neighbors(r, c, n_rows, n_cols, connectivity):
                if not in_mask[qr, qc]:
                    continue
                q_label = int(labels[qr, qc])
                if q_label == UNLABELED:
                    labels[qr, qc] = p_label
                    heapq.heappush(heap, (float(elev[qr, qc]), qr * n_cols + qc))
                elif q_label != p_label and q_label != WATERSHED_LINE:
                    # Ridge between two basins. Seeds are never demoted;
                    # their conflicts are only recorded.
                    if seed_pixel[r, c]:
                        seed_conflict_count += 1
                    else:
                        labels[r, c] = WATERSHED_LINE
                    conflict_count += 1
                    break
            processed += 1
        chunk_index += 1
        if on_progress is not None:
            on_progress(chunk_index, processed, total, float(level))

    boundary = labels == WATERSHED_LINE
    stats = WatershedStats(
        pixels_total=int(elev.size),
        pixels_in_mask=total,
        pixels_labeled=int(((labels > 0) & in_mask).sum()),
        pixels_watershed=int(boundary.sum()),
        pixels_unlabeled=int((labels == UNLABELED).sum()),
        seed_count=int(seed_pixel.sum()),
        label_count=int(len(np.unique(marks[seed_pixel]))),
        conflict_count=conflict_count,
        seed_conflict_count=seed_conflict_count,
        chunks_processed=chunk_index,
    )
    return WatershedResult(labels=labels, boundary=boundary, stats=stats)
