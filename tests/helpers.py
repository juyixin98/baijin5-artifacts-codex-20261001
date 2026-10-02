"""Shared helpers for kernel tests."""

from __future__ import annotations

import numpy as np

from watershed_backend.contracts import SeedPoint, build_markers_from_seeds
from watershed_backend.kernel import WatershedResult, flood_watershed


def run_from_seeds(
    gradient: np.ndarray,
    seeds: list[SeedPoint],
    connectivity: int = 8,
    mask: np.ndarray | None = None,
) -> WatershedResult:
    markers = build_markers_from_seeds(seeds, gradient.shape)
    return flood_watershed(gradient, markers, connectivity=connectivity, mask=mask)


def assert_boundary_separates(labels: np.ndarray, connectivity: int = 8) -> None:
    """No two pixels with different positive labels may be neighbours."""
    offsets = [(-1, 0), (0, -1)] if connectivity == 4 else [
        (-1, -1), (-1, 0), (-1, 1), (0, -1)
    ]
    height, width = labels.shape
    for dr, dc in offsets:
        for r in range(height):
            nr = r + dr
            if not (0 <= nr < height):
                continue
            for c in range(width):
                nc = c + dc
                if not (0 <= nc < width):
                    continue
                a, b = labels[r, c], labels[nr, nc]
                if a > 0 and b > 0:
                    assert a == b, (
                        f"labels {a} and {b} touch at ({r},{c})-({nr},{nc}) "
                        "without a ridge between them"
                    )
