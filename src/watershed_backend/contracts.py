"""Image data contract: turns raw request payloads into validated kernel input.

The contract fixes, for every run:

* gradient: 2-D, finite, float64-convertible, within the configured pixel cap
* markers: int32, same shape as gradient, labels >= 1, at least one seed
* seeds (alternative marker source): deduplicated; the same pixel claimed by
  two different labels is a ``SEED_CONFLICT`` and rejected, never resolved
  silently
* mask: bool, same shape; seeds must lie on active pixels
* connectivity: 4 or 8
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, NamedTuple

import numpy as np

from .config import Settings
from .errors import (
    EmptyMarkers,
    ImageTooLarge,
    InvalidConnectivity,
    InvalidLabel,
    NonFiniteGradient,
    SeedConflict,
    SeedOutOfBounds,
    SeedOutsideMask,
    ShapeMismatch,
)


class SeedPoint(NamedTuple):
    row: int
    col: int
    label: int


@dataclass(frozen=True)
class SegmentationInput:
    """Validated, kernel-ready input plus provenance for logging."""

    gradient: np.ndarray  # float64, 2-D, finite
    markers: np.ndarray  # int32, same shape, >= 1 positive label
    connectivity: int
    mask: np.ndarray | None  # bool or None
    seed_count: int
    marker_labels: tuple[int, ...]

    @property
    def shape(self) -> tuple[int, int]:
        return self.gradient.shape  # type: ignore[return-value]


def build_markers_from_seeds(
    seeds: Iterable[SeedPoint], shape: tuple[int, int]
) -> np.ndarray:
    """Materialise a marker array from seed points.

    Deterministic: seeds are sorted in row-major order before stamping, so
    the permutation of the incoming seed list cannot affect the result.
    Duplicate seeds with the *same* label collapse; duplicates with
    *different* labels are a conflict and abort the request.
    """
    height, width = shape
    by_pixel: dict[tuple[int, int], int] = {}
    for seed in seeds:
        if seed.label < 1:
            raise InvalidLabel(f"seed label must be >= 1, got {seed.label}")
        if not (0 <= seed.row < height and 0 <= seed.col < width):
            raise SeedOutOfBounds(
                f"seed ({seed.row}, {seed.col}) outside image of shape {shape}"
            )
        key = (seed.row, seed.col)
        previous = by_pixel.get(key)
        if previous is not None and previous != seed.label:
            raise SeedConflict(
                f"pixel {key} claimed by both label {previous} and label {seed.label}"
            )
        by_pixel[key] = seed.label

    markers = np.zeros(shape, dtype=np.int32)
    for (row, col), label in sorted(by_pixel.items()):
        markers[row, col] = label
    return markers


def validate_segmentation_input(
    gradient: np.ndarray,
    markers: np.ndarray,
    connectivity: int,
    mask: np.ndarray | None,
    settings: Settings,
) -> SegmentationInput:
    """Validate raw arrays against the contract; raise typed errors."""
    grad = np.asarray(gradient, dtype=np.float64)
    marks = np.asarray(markers, dtype=np.int32)

    if grad.ndim != 2:
        raise ShapeMismatch(f"gradient must be 2-D, got {grad.ndim}-D")
    if marks.shape != grad.shape:
        raise ShapeMismatch(
            f"markers shape {marks.shape} != gradient shape {grad.shape}"
        )
    if grad.size > settings.max_pixels:
        raise ImageTooLarge(
            f"image has {grad.size} pixels, limit is {settings.max_pixels}"
        )
    if not np.isfinite(grad).all():
        raise NonFiniteGradient("gradient contains NaN or infinite values")
    if connectivity not in (4, 8):
        raise InvalidConnectivity(f"connectivity must be 4 or 8, got {connectivity}")
    if marks.min() < 0:
        raise InvalidLabel("marker labels must be >= 0 (0 = unmarked)")

    active_mask: np.ndarray | None = None
    if mask is not None:
        active_mask = np.asarray(mask, dtype=bool)
        if active_mask.shape != grad.shape:
            raise ShapeMismatch(
                f"mask shape {active_mask.shape} != gradient shape {grad.shape}"
            )

    if active_mask is not None and bool(((marks > 0) & ~active_mask).any()):
        raise SeedOutsideMask("seed markers found on masked-out pixels")
    seed_positions = marks > 0
    if active_mask is not None:
        seed_positions &= active_mask
    seed_count = int(seed_positions.sum())
    if seed_count == 0:
        raise EmptyMarkers("no seed markers on active pixels")

    marker_labels = tuple(int(v) for v in np.unique(marks[marks > 0]))
    return SegmentationInput(
        gradient=grad,
        markers=marks,
        connectivity=connectivity,
        mask=active_mask,
        seed_count=seed_count,
        marker_labels=marker_labels,
    )
