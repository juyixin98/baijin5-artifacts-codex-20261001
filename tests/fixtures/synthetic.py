"""Synthetic local fixtures.

The expected arrays for ``two_basin``, ``flat_plateau`` and ``saddle`` are
hand-computed from the documented flooding rules (level-by-level immersion,
row-major tie-break, ridge where two fronts meet) — they are written down
here as literals, not produced by running the kernel, so they form an
independent reference.
"""

from __future__ import annotations

import numpy as np

from watershed_backend.contracts import SeedPoint


def two_basin() -> tuple[np.ndarray, list[SeedPoint], np.ndarray, np.ndarray]:
    """Two minima separated by a high ridge; asymmetric hand-traced flood.

    Note the immersion subtlety this fixture pins down: (1,3) and (3,1)
    have elevation 2, lower than the elevation-5 pixels queued earlier, so
    the flood reaches them *before* the level-5 front advances — basin 1
    claims both, and the ridge forms along row 2 / column 2 instead of the
    diagonal.  The expected array below was traced by hand under exactly
    the documented (elevation, insertion-order) rule.
    """
    gradient = np.array(
        [
            [8, 7, 6, 7, 8],
            [7, 1, 5, 2, 7],
            [6, 5, 9, 5, 6],
            [7, 2, 5, 1, 7],
            [8, 7, 6, 7, 8],
        ],
        dtype=np.float64,
    )
    seeds = [SeedPoint(1, 1, 1), SeedPoint(3, 3, 2)]
    expected_labels = np.array(
        [
            [1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1],
            [1, 1, 0, 0, 0],
            [1, 1, 0, 2, 2],
            [1, 1, 0, 2, 2],
        ],
        dtype=np.int32,
    )
    expected_boundary = expected_labels == 0
    return gradient, seeds, expected_labels, expected_boundary


def flat_plateau() -> tuple[np.ndarray, list[SeedPoint], np.ndarray, np.ndarray]:
    """Uniform plateau between two seeds: ridge must sit exactly in column 3.

    With the deterministic tie-break, basin 1 floods columns 0-2 and basin 2
    floods columns 4-6; the fronts meet on column 3, which becomes ridge.
    """
    gradient = np.array(
        [
            [5, 5, 5, 5, 5, 5, 5],
            [1, 5, 5, 5, 5, 5, 1],
            [5, 5, 5, 5, 5, 5, 5],
        ],
        dtype=np.float64,
    )
    seeds = [SeedPoint(1, 0, 1), SeedPoint(1, 6, 2)]
    expected_labels = np.array(
        [
            [1, 1, 1, 0, 2, 2, 2],
            [1, 1, 1, 0, 2, 2, 2],
            [1, 1, 1, 0, 2, 2, 2],
        ],
        dtype=np.int32,
    )
    expected_boundary = expected_labels == 0
    return gradient, seeds, expected_labels, expected_boundary


def saddle() -> tuple[np.ndarray, list[SeedPoint], np.ndarray, np.ndarray]:
    """Diagonal seeds on a saddle surface; unseeded local minima get absorbed.

    (0,2) and (2,0) are elevation-1 minima without seeds.  The flood from
    seed 1 reaches them before any other front, so they join basin 1; the
    saddle pixels (1,1), (1,2), (2,1) see both fronts and become ridge.
    """
    gradient = np.array(
        [
            [1, 2, 1],
            [2, 3, 2],
            [1, 2, 1],
        ],
        dtype=np.float64,
    )
    seeds = [SeedPoint(0, 0, 1), SeedPoint(2, 2, 2)]
    expected_labels = np.array(
        [
            [1, 1, 1],
            [1, 0, 0],
            [1, 0, 2],
        ],
        dtype=np.int32,
    )
    expected_boundary = expected_labels == 0
    return gradient, seeds, expected_labels, expected_boundary


def noisy_gradient(
    seed: int = 20260927, size: int = 16
) -> tuple[np.ndarray, list[SeedPoint]]:
    """Fixed-seed noisy gradient; used for invariant and regression checks."""
    rng = np.random.default_rng(seed)
    gradient = rng.integers(0, 256, size=(size, size)).astype(np.float64)
    seeds = [
        SeedPoint(2, 2, 1),
        SeedPoint(13, 13, 2),
        SeedPoint(2, 13, 3),
        SeedPoint(13, 2, 4),
    ]
    return gradient, seeds
