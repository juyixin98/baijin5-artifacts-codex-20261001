"""Kernel tests: hand-derived paths + exhaustive small-image verification.

Two independent oracles live in THIS file, not in the package:

* ``enumerate_all_seams`` -- brute-force enumeration of every legal seam;
  proves the kernel's energy equals the true minimum (optimality).
* ``reference_dp`` -- a second, deliberately different implementation of
  the documented DP + tie-break spec (pure-Python recursion); proves the
  kernel returns the exact seam the spec selects (conformance).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from seamcarve.energy import forward_energy_components, gradient_energy
from seamcarve.kernel import (
    MAX_STEP,
    PREDECESSOR_PREFERENCE,
    find_seam,
    find_vertical_seam_forward,
    find_vertical_seam_gradient,
)

HAND_4x4 = np.array(
    [
        [10, 20, 30, 40],
        [10, 20, 30, 40],
        [50, 60, 70, 80],
        [50, 60, 70, 80],
    ],
    dtype=np.uint8,
)


# ---------------------------------------------------------------- oracles

def enumerate_all_seams(height: int, width: int, mask: np.ndarray):
    """Every legal seam as a tuple of columns (respects MAX_STEP and mask)."""
    seams = []

    def walk(row: int, col: int, path: list[int]):
        if mask[row, col]:
            return
        path.append(col)
        if row == height - 1:
            seams.append(tuple(path))
        else:
            for step in range(-MAX_STEP, MAX_STEP + 1):
                nxt = col + step
                if 0 <= nxt < width:
                    walk(row + 1, nxt, path)
        path.pop()

    for start in range(width):
        walk(0, start, [])
    return seams


def seam_cost_gradient(energy: np.ndarray, columns: tuple[int, ...]) -> float:
    return float(sum(energy[i, c] for i, c in enumerate(columns)))


def seam_cost_forward(cu, cl, cr, columns: tuple[int, ...]) -> float:
    total = 0.0
    for i in range(1, len(columns)):
        step = columns[i] - columns[i - 1]
        add = {0: cu, 1: cl, -1: cr}[step]
        total += add[i, columns[i]]
    return total


def reference_dp(add_rows, base, mask) -> tuple[tuple[int, ...], float]:
    """Independent spec implementation: cell-by-cell recursion, documented
    tie-break (predecessor preference, then leftmost final column)."""
    height, width = mask.shape
    cost = [[math.inf] * width for _ in range(height)]
    choice = [[0] * width for _ in range(height)]
    for j in range(width):
        if not mask[0, j]:
            cost[0][j] = base[j]
    for i in range(1, height):
        ac, al, ar = add_rows[i - 1]
        for j in range(width):
            if mask[i, j]:
                continue
            candidates = {0: cost[i - 1][j] + ac[j]}
            if j > 0:
                candidates[-1] = cost[i - 1][j - 1] + al[j]
            if j < width - 1:
                candidates[1] = cost[i - 1][j + 1] + ar[j]
            best, best_off = math.inf, 0
            for off in PREDECESSOR_PREFERENCE:
                if off in candidates and candidates[off] < best:
                    best, best_off = candidates[off], off
            cost[i][j] = best
            choice[i][j] = best_off
    col = min(range(width), key=lambda j: (cost[height - 1][j], j))
    if not math.isfinite(cost[height - 1][col]):
        return (), math.inf
    cols = [0] * height
    for i in range(height - 1, 0, -1):
        cols[i] = col
        col += choice[i][col]
    cols[0] = col
    return tuple(cols), cost[height - 1][cols[-1]]


# ------------------------------------------------------- hand-derived cases

def test_gradient_hand_4x4_exact_path_and_energy(run_logger):
    """Hand-derived: min seam is straight down column 0, energy 120."""
    result = find_seam(HAND_4x4, np.zeros((4, 4), bool), "gradient")
    run_logger.emit(
        "assertion", step="dp", basis="hand-derived DP sweep",
        input="hand_4x4", mode="gradient",
        expected_columns=[0, 0, 0, 0], expected_energy=120.0,
        got_columns=list(result.columns), got_energy=result.energy,
    )
    assert result.columns == (0, 0, 0, 0)
    assert result.energy == pytest.approx(120.0)


def test_forward_hand_4x4_exact_path_and_energy(run_logger):
    """Hand-derived forward DP: min seam is column 0, energy 30."""
    result = find_seam(HAND_4x4, np.zeros((4, 4), bool), "forward")
    run_logger.emit(
        "assertion", step="dp", basis="hand-derived forward DP sweep",
        input="hand_4x4", mode="forward",
        expected_columns=[0, 0, 0, 0], expected_energy=30.0,
        got_columns=list(result.columns), got_energy=result.energy,
    )
    assert result.columns == (0, 0, 0, 0)
    assert result.energy == pytest.approx(30.0)


# ---------------------------------------------------- exhaustive verification

@pytest.mark.parametrize("seed", range(6))
def test_gradient_matches_brute_force_minimum(seed, run_logger):
    rng = np.random.default_rng(100 + seed)
    image = rng.integers(0, 256, size=(5, 4), dtype=np.uint8)
    mask = np.zeros((5, 4), bool)
    energy = gradient_energy(image)
    result = find_vertical_seam_gradient(energy, mask)

    seams = enumerate_all_seams(5, 4, mask)
    brute_min = min(seam_cost_gradient(energy, s) for s in seams)
    ref_cols, ref_cost = reference_dp(
        [(energy[i], energy[i], energy[i]) for i in range(1, 5)], energy[0], mask
    )
    run_logger.emit(
        "assertion", step="dp", basis="brute-force enumeration + reference DP",
        seed=seed, n_seams_enumerated=len(seams),
        brute_min=brute_min, kernel_energy=result.energy,
        kernel_columns=list(result.columns), reference_columns=list(ref_cols),
    )
    assert result.energy == pytest.approx(brute_min)       # optimal
    assert result.energy == pytest.approx(ref_cost)
    assert result.columns == ref_cols                       # exact spec path
    assert result.columns in seams                          # legal
    assert all(abs(a - b) <= MAX_STEP for a, b in zip(result.columns, result.columns[1:]))


@pytest.mark.parametrize("seed", range(6))
def test_forward_matches_brute_force_minimum(seed, run_logger):
    rng = np.random.default_rng(200 + seed)
    image = rng.integers(0, 256, size=(5, 4), dtype=np.uint8)
    mask = np.zeros((5, 4), bool)
    cu, cl, cr = forward_energy_components(image)
    result = find_vertical_seam_forward(cu, cl, cr, mask)

    seams = enumerate_all_seams(5, 4, mask)
    brute_min = min(seam_cost_forward(cu, cl, cr, s) for s in seams)
    ref_cols, ref_cost = reference_dp(
        [(cu[i], cl[i], cr[i]) for i in range(1, 5)], np.zeros(4), mask
    )
    run_logger.emit(
        "assertion", step="dp", basis="brute-force enumeration + reference DP",
        seed=seed, n_seams_enumerated=len(seams),
        brute_min=brute_min, kernel_energy=result.energy,
        kernel_columns=list(result.columns), reference_columns=list(ref_cols),
    )
    assert result.energy == pytest.approx(brute_min)
    assert result.columns == ref_cols
    assert result.columns in seams


def test_displacement_limit_is_one_on_fixtures(fixture_loader):
    """Adjacent-row displacement never exceeds MAX_STEP on real fixtures."""
    for name in ("grad_8x6", "rgb_10x8", "random_12x10"):
        image, mask, _ = fixture_loader(name)
        mask = mask if mask is not None else np.zeros(image.shape[:2], bool)
        for mode in ("gradient", "forward"):
            result = find_seam(image, mask, mode)
            steps = [abs(a - b) for a, b in zip(result.columns, result.columns[1:])]
            assert all(s <= MAX_STEP for s in steps), (name, mode, steps)
