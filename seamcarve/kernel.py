"""Numeric core: dynamic-programming vertical seam search.

Algorithmic contract (acceptance rule 1)
----------------------------------------
* Adjacent-row displacement is explicitly limited: a seam moves at most
  ``MAX_STEP = 1`` column between consecutive rows.
* Gradient energy and forward energy are separate DP recurrences:

  - gradient:  cost[i, j] = energy[i, j] + min(cost[i-1, j-1],
                                               cost[i-1, j],
                                               cost[i-1, j+1])
               base row: cost[0, :] = energy[0, :]

  - forward:   cost[i, j] = min(cost[i-1, j-1] + CL[i, j],
                                cost[i-1, j]   + CU[i, j],
                                cost[i-1, j+1] + CR[i, j])
               base row: cost[0, :] = 0

* Protected pixels can never be on a seam: their accumulated cost is
  forced to +inf. If the final row minimum is not finite, every legal
  path crosses the protected region and ``NoLegalSeamError`` is raised
  (acceptance rule 2).

Tie-breaking (deterministic, documented)
----------------------------------------
* Predecessor preference within a cell: straight (offset 0), then
  up-left (offset -1), then up-right (offset +1) -- strict ``<`` scans in
  ``PREDECESSOR_PREFERENCE`` order.
* Final-column ties break to the leftmost column (numpy argmin).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .energy import forward_energy_components, gradient_energy
from .errors import ContractViolationError, NoLegalSeamError

MAX_STEP = 1
#: order in which predecessor candidates win ties: straight, up-left, up-right
PREDECESSOR_PREFERENCE = (0, -1, 1)


@dataclass(frozen=True)
class SeamResult:
    """A seam in CURRENT-image coordinates (columns per row, top to bottom)."""

    columns: tuple[int, ...]
    energy: float
    mode: str


def _check_energy_and_mask(energy: np.ndarray, protect_mask: np.ndarray) -> None:
    if energy.ndim != 2:
        raise ContractViolationError(f"energy must be 2D, got ndim={energy.ndim}")
    if protect_mask.shape != energy.shape:
        raise ContractViolationError(
            f"protect_mask shape {protect_mask.shape} != energy shape {energy.shape}"
        )
    if protect_mask.dtype != bool:
        raise ContractViolationError("protect_mask must be boolean")


def _run_dp(
    base: np.ndarray,
    add_rows: list[tuple[np.ndarray, np.ndarray, np.ndarray]],
    protect_mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Shared DP sweep.

    ``base`` is the 1D cost of row 0. ``add_rows[i-1]`` holds the per-cell
    added costs (center, up-left, up-right) for transitioning into row i.
    Returns (cost, choice) where choice[i, j] is the predecessor column
    offset in {-1, 0, +1}.
    """
    height, width = protect_mask.shape
    cost = np.empty((height, width), dtype=np.float64)
    choice = np.zeros((height, width), dtype=np.int8)

    row0 = base.astype(np.float64, copy=True)
    row0[protect_mask[0]] = np.inf
    cost[0] = row0

    for i in range(1, height):
        add_center, add_left, add_right = add_rows[i - 1]
        prev = cost[i - 1]

        candidates = {
            0: prev + add_center,
            -1: np.concatenate(([np.inf], prev[:-1] + add_left[1:])),
            1: np.concatenate((prev[1:] + add_right[:-1], [np.inf])),
        }
        best = np.full(width, np.inf)
        chosen = np.zeros(width, dtype=np.int8)
        for offset in PREDECESSOR_PREFERENCE:
            better = candidates[offset] < best  # strict: earlier preference wins ties
            best = np.where(better, candidates[offset], best)
            chosen = np.where(better, offset, chosen)

        best[protect_mask[i]] = np.inf
        cost[i] = best
        choice[i] = chosen
    return cost, choice


def _backtrack(cost: np.ndarray, choice: np.ndarray, mode: str) -> SeamResult:
    last = cost[-1]
    col = int(np.argmin(last))  # leftmost on ties
    if not np.isfinite(last[col]):
        raise NoLegalSeamError(
            "no legal seam: every top-to-bottom path crosses a protected pixel"
        )
    height = cost.shape[0]
    columns = [0] * height
    for i in range(height - 1, 0, -1):
        columns[i] = col
        col += int(choice[i, col])
    columns[0] = col
    return SeamResult(columns=tuple(columns), energy=float(last[columns[-1]]), mode=mode)


def find_vertical_seam_gradient(
    energy: np.ndarray, protect_mask: np.ndarray
) -> SeamResult:
    """Min-energy vertical seam over an ordinary gradient energy map."""
    energy = np.asarray(energy, dtype=np.float64)
    _check_energy_and_mask(energy, protect_mask)
    height = energy.shape[0]
    add_rows = [(energy[i], energy[i], energy[i]) for i in range(1, height)]
    cost, choice = _run_dp(energy[0], add_rows, protect_mask)
    return _backtrack(cost, choice, mode="gradient")


def find_vertical_seam_forward(
    cu: np.ndarray,
    cl: np.ndarray,
    cr: np.ndarray,
    protect_mask: np.ndarray,
) -> SeamResult:
    """Min-energy vertical seam under Avidan-Shamir forward energy."""
    cu = np.asarray(cu, dtype=np.float64)
    cl = np.asarray(cl, dtype=np.float64)
    cr = np.asarray(cr, dtype=np.float64)
    _check_energy_and_mask(cu, protect_mask)
    if cl.shape != cu.shape or cr.shape != cu.shape:
        raise ContractViolationError("CU/CL/CR shapes must match")
    height = cu.shape[0]
    add_rows = [(cu[i], cl[i], cr[i]) for i in range(1, height)]
    base = np.zeros(cu.shape[1], dtype=np.float64)
    cost, choice = _run_dp(base, add_rows, protect_mask)
    return _backtrack(cost, choice, mode="forward")


def find_seam(
    image: np.ndarray,
    protect_mask: np.ndarray,
    mode: str,
    run_logger=None,
) -> SeamResult:
    """Compute energy from the current image and find the minimal seam.

    Energy is recomputed from the *current* image on every call -- callers
    must never reuse a stale energy map after removals (acceptance rule 3).
    """
    if mode == "gradient":
        result = find_vertical_seam_gradient(gradient_energy(image), protect_mask)
    elif mode == "forward":
        cu, cl, cr = forward_energy_components(image)
        result = find_vertical_seam_forward(cu, cl, cr, protect_mask)
    else:
        raise ContractViolationError(f"unknown energy mode: {mode!r}")
    if run_logger is not None:
        run_logger.emit(
            "seam_found",
            step="dp",
            mode=mode,
            image_shape=list(image.shape),
            columns=list(result.columns),
            energy=result.energy,
            decision=(
                "dp min over last row; ties: predecessor preference "
                f"{PREDECESSOR_PREFERENCE}, final column leftmost"
            ),
        )
    return result
