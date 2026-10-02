"""Synthetic fixtures for the watershed test-suite.

All fixtures are small, fully local and deterministic. Hand-computed
expected arrays live next to the fixtures so a test failure shows the exact
disagreement between the documented algorithm semantics and the kernel.
"""

from __future__ import annotations

import numpy as np

from app.core.watershed import WATERSHED_LINE as W

# ---------------------------------------------------------------------------
# 1-D two-basin case (hand-computed, 4-connectivity).
# Flood order: level 1 seed 1 -> level 2 seed 2 -> level 3 -> level 4, where
# basin 2 meets basin 1 and the meeting pixel becomes the ridge.
# ---------------------------------------------------------------------------
TWO_BASINS_1D_ELEVATION = np.array([[1.0, 3.0, 5.0, 4.0, 2.0]])
TWO_BASINS_1D_MARKERS = np.array([[1, 0, 0, 0, 2]])
TWO_BASINS_1D_EXPECTED = np.array([[1, 1, 1, W, 2]])

# ---------------------------------------------------------------------------
# Flat plateau (hand-computed, 4-connectivity).
# All elevations equal: the tie-break (ascending flat index) decides the
# processing order, so basin 1 floods left-to-right and the pixel that meets
# basin 2 becomes the ridge. This must NOT depend on queue accidents.
# ---------------------------------------------------------------------------
PLATEAU_ELEVATION = np.full((1, 5), 2.0)
PLATEAU_MARKERS = np.array([[1, 0, 0, 0, 2]])
PLATEAU_EXPECTED = np.array([[1, 1, 1, W, 2]])

# 2-D plateau, 4-connectivity (hand-computed).
PLATEAU_2D_ELEVATION = np.full((3, 3), 5.0)
PLATEAU_2D_MARKERS = np.array([[0, 0, 0], [1, 0, 2], [0, 0, 0]])
PLATEAU_2D_EXPECTED = np.array([[1, 1, W], [1, W, 2], [1, W, 2]])

# ---------------------------------------------------------------------------
# Corridor case (hand-computed, 4-connectivity): a low-elevation corridor on
# the right lets basin 2 flood pixels that are Euclidean-closer to seed 1.
# Proves the output is water-level propagation, not nearest-seed distance.
# ---------------------------------------------------------------------------
CORRIDOR_ELEVATION = np.array(
    [
        [1.0, 9.0, 9.0, 9.0, 9.0, 2.0],
        [9.0, 3.0, 3.0, 3.0, 3.0, 3.0],
    ]
)
CORRIDOR_MARKERS = np.array(
    [
        [1, 0, 0, 0, 0, 2],
        [0, 0, 0, 0, 0, 0],
    ]
)
CORRIDOR_EXPECTED = np.array(
    [
        [1, W, 2, 2, 2, 2],
        [1, W, 2, 2, 2, 2],
    ]
)
# Pixel (1, 2) is Euclidean-closer to seed (0, 0) than to seed (0, 5) yet is
# flooded by basin 2 through the low corridor.
CORRIDOR_DISTANCE_CONTRADICTION_PIXEL = (1, 2)

# ---------------------------------------------------------------------------
# Symmetric two-basin field (7x7): elevation = min of two quadratic wells.
# Hand-computed for 4-connectivity: the geometric tie column (3) is claimed
# by basin 1 (lower flat index pops first), so the ridge forms one column to
# the right, on column 4 — the deterministic consequence of the documented
# tie-break rule, not of queue order.
# ---------------------------------------------------------------------------
def two_basins() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows, cols = 7, 7
    elevation = np.empty((rows, cols))
    for r in range(rows):
        for c in range(cols):
            elevation[r, c] = min((r - 3) ** 2 + (c - 1) ** 2, (r - 3) ** 2 + (c - 5) ** 2)
    markers = np.zeros((rows, cols), dtype=np.int64)
    markers[3, 1] = 1
    markers[3, 5] = 2
    expected = np.empty((rows, cols), dtype=np.int64)
    expected[:, 0:4] = 1
    expected[:, 4] = W
    expected[:, 5:7] = 2
    return elevation, markers, expected

# ---------------------------------------------------------------------------
# Saddle field (5x5, hand-computed for 4-connectivity). Low arms at the
# middle row edges, saddle point at the centre (elevation 8). The index
# tie-break gives the saddle pixel to basin 1; the ridge forms on column 3.
# ---------------------------------------------------------------------------
def saddle() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    elevation = np.empty((5, 5))
    for r in range(5):
        for c in range(5):
            elevation[r, c] = 2 * (r - 2) ** 2 - (c - 2) ** 2 + 8
    markers = np.zeros((5, 5), dtype=np.int64)
    markers[2, 0] = 1
    markers[2, 4] = 2
    expected = np.array(
        [
            [1, 1, 1, W, 2],
            [1, 1, 1, W, 2],
            [1, 1, 1, W, 2],
            [1, 1, 1, W, 2],
            [1, 1, 1, W, 2],
        ]
    )
    return elevation, markers, expected

# ---------------------------------------------------------------------------
# Noisy gradient (16x16): two wells plus deterministic uniform noise.
# Used for property tests (seed preservation, connectivity, stability);
# correctness is cross-checked against the independent reference flood.
# ---------------------------------------------------------------------------
NOISE_SEED = 20261002

def noisy_gradient() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(NOISE_SEED)
    rows, cols = 16, 16
    elevation = np.empty((rows, cols))
    for r in range(rows):
        for c in range(cols):
            elevation[r, c] = min((r - 8) ** 2 + (c - 4) ** 2, (r - 8) ** 2 + (c - 12) ** 2)
    elevation = elevation + rng.uniform(0.0, 0.5, size=(rows, cols))
    markers = np.zeros((rows, cols), dtype=np.int64)
    markers[8, 4] = 1
    markers[8, 12] = 2
    return elevation, markers

# ---------------------------------------------------------------------------
# Conflicting adjacent seeds (hand-computed): seeds are never demoted, the
# conflict is recorded instead of producing a ridge pixel.
# ---------------------------------------------------------------------------
CONFLICT_ELEVATION = np.array([[2.0, 2.0]])
CONFLICT_MARKERS = np.array([[1, 2]])
CONFLICT_EXPECTED = np.array([[1, 2]])

# ---------------------------------------------------------------------------
# Masked (seedless) region: pixels outside the mask stay UNLABELED.
# ---------------------------------------------------------------------------
MASK_ELEVATION = np.ones((1, 5))
MASK_MARKERS = np.array([[1, 0, 0, 0, 0]])
MASK_MASK = np.array([[True, True, False, False, False]])
MASK_EXPECTED = np.array([[1, 1, 0, 0, 0]])
