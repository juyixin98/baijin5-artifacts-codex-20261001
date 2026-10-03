"""Dynamic-programming seam kernel.

The adjacent-row displacement limit is an explicit parameter
(``max_displacement``): a seam pixel at column ``j`` in row ``i`` may only
be preceded by a pixel in columns ``[j-D, j+D]`` of row ``i-1``.

Two independent recurrences live here:

* gradient mode — ``M(i,j) = E(i,j) + min_{|p-j|<=D} M(i-1,p)`` over a
  precomputed gradient-energy surface;
* forward mode — the Avidan-Shamir forward-energy recurrence over
  CU/CL/CR transition costs (defined for D == 1 only, enforced by
  :class:`app.config.Settings`).

Protection: protected pixels carry infinite energy / infinite arrival
cost, so no minimum-energy path can cross them.  If an entire DP row
becomes unreachable (every entry infinite) no legal seam exists and
:class:`NoLegalSeamError` is raised — callers must not silently degrade.

Tie-breaking is deterministic: among equally cheap predecessors the
leftmost column wins, and among equally cheap final-row endpoints the
leftmost column wins.  Tests rely on this rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.config import ENERGY_FORWARD, ENERGY_GRADIENT, Settings
from app.energy import forward_costs, gradient_energy, to_luminance
from app.errors import InvalidImageError, InvalidRequestError, MaskShapeError, NoLegalSeamError


@dataclass(frozen=True)
class SeamResult:
    """One minimum-energy seam on the energy surface it was computed from."""

    path: tuple[int, ...]  # column index per row, len == image height
    energy: float
    mode: str
    max_displacement: int
    start_col: int
    final_row_ties: int  # how many final-row columns share the minimum
    diagnostics: dict = field(default_factory=dict)


def _validate_inputs(surface: np.ndarray, protected_mask: np.ndarray | None) -> np.ndarray:
    arr = np.asarray(surface)
    if arr.ndim != 2 or arr.shape[0] < 1 or arr.shape[1] < 1:
        raise InvalidImageError(
            "energy surface must be a non-empty 2-D array",
            details={"shape": list(arr.shape)},
        )
    if protected_mask is None:
        return np.zeros(arr.shape, dtype=bool)
    mask = np.asarray(protected_mask, dtype=bool)
    if mask.shape != arr.shape:
        raise MaskShapeError(
            "protected mask shape must match the image",
            details={"surface_shape": list(arr.shape), "mask_shape": list(mask.shape)},
        )
    return mask


def _check_legal(cumulative: np.ndarray, row: int) -> None:
    if np.isinf(cumulative[row]).all():
        raise NoLegalSeamError(
            "protection mask blocks every seam",
            details={"blocked_at_row": row, "width": int(cumulative.shape[1])},
        )


def _backtrack(cumulative: np.ndarray, back: np.ndarray) -> tuple[tuple[int, ...], int, int]:
    last = cumulative[-1]
    min_energy = float(last.min())
    if not np.isfinite(min_energy):
        raise NoLegalSeamError(
            "protection mask blocks every seam",
            details={"blocked_at_row": int(cumulative.shape[0]) - 1},
        )
    ties = int(np.count_nonzero(last == min_energy))
    col = int(np.argmin(last))  # leftmost minimum wins
    path = []
    for row in range(cumulative.shape[0] - 1, -1, -1):
        path.append(col)
        col = int(back[row, col])
        if col < 0 and row > 0:  # pragma: no cover - defensive
            raise NoLegalSeamError("backtracking hit an unreachable cell")
    path.reverse()
    return tuple(path), col, ties


def _accumulate_gradient(energy: np.ndarray, max_displacement: int) -> tuple[np.ndarray, np.ndarray]:
    height, width = energy.shape
    cumulative = energy.astype(np.float64).copy()
    back = np.zeros((height, width), dtype=np.int64)
    cols = np.arange(width)
    for row in range(1, height):
        best = np.full(width, np.inf)
        best_from = np.full(width, -1, dtype=np.int64)
        # Offsets ascending => leftmost predecessor considered first;
        # strict "<" keeps it on ties (deterministic tie-break).
        for offset in range(-max_displacement, max_displacement + 1):
            preds = cols + offset
            valid = (preds >= 0) & (preds < width)
            candidate = np.where(valid, cumulative[row - 1, np.clip(preds, 0, width - 1)], np.inf)
            update = candidate < best
            best = np.where(update, candidate, best)
            best_from = np.where(update, preds, best_from)
        cumulative[row] = energy[row] + best
        back[row] = np.where(best_from < 0, cols, best_from)
        _check_legal(cumulative, row)
    return cumulative, back


def _accumulate_forward(
    cu: np.ndarray, cl: np.ndarray, cr: np.ndarray, first_row_blocked: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    height, width = cu.shape
    cumulative = np.zeros((height, width), dtype=np.float64)
    # A protected pixel in row 0 is never "entered" by a transition, so the
    # ban must be seeded into the initial cumulative row explicitly.
    cumulative[0] = np.where(first_row_blocked, np.inf, 0.0)
    _check_legal(cumulative, 0)
    back = np.zeros((height, width), dtype=np.int64)
    cols = np.arange(width)
    for row in range(1, height):
        prev = cumulative[row - 1]
        best = np.full(width, np.inf)
        best_from = np.full(width, -1, dtype=np.int64)
        # Predecessors in ascending column order: j-1 (CR), j (CU), j+1 (CL).
        for offset, cost in ((-1, cr[row]), (0, cu[row]), (1, cl[row])):
            preds = cols + offset
            valid = (preds >= 0) & (preds < width)
            shifted = prev[np.clip(preds, 0, width - 1)]
            candidate = np.where(valid, shifted + cost, np.inf)
            update = candidate < best
            best = np.where(update, candidate, best)
            best_from = np.where(update, preds, best_from)
        cumulative[row] = best
        back[row] = np.where(best_from < 0, cols, best_from)
        _check_legal(cumulative, row)
    return cumulative, back


def find_seam_gradient(
    energy: np.ndarray,
    protected_mask: np.ndarray | None = None,
    max_displacement: int = 1,
) -> SeamResult:
    """Minimum-energy seam over a precomputed gradient-energy surface."""
    if max_displacement < 1:
        raise InvalidRequestError(
            "max_displacement must be >= 1",
            details={"max_displacement": max_displacement},
        )
    mask = _validate_inputs(energy, protected_mask)
    protected_energy = np.where(mask, np.inf, np.asarray(energy, dtype=np.float64))
    cumulative, back = _accumulate_gradient(protected_energy, max_displacement)
    path, _, ties = _backtrack(cumulative, back)
    return SeamResult(
        path=path,
        energy=float(cumulative[-1, path[-1]]),
        mode=ENERGY_GRADIENT,
        max_displacement=max_displacement,
        start_col=path[0],
        final_row_ties=ties,
        diagnostics={
            "height": int(energy.shape[0]),
            "width": int(energy.shape[1]),
            "protected_pixels": int(mask.sum()),
        },
    )


def find_seam_forward(
    luminance: np.ndarray,
    protected_mask: np.ndarray | None = None,
) -> SeamResult:
    """Minimum forward-energy seam (Avidan-Shamir), displacement fixed at 1."""
    lum = np.asarray(luminance, dtype=np.float64)
    mask = _validate_inputs(lum, protected_mask)
    cu, cl, cr = forward_costs(lum)
    # A protected pixel may not be *entered*: every arrival cost is infinite.
    cu = np.where(mask, np.inf, cu)
    cl = np.where(mask, np.inf, cl)
    cr = np.where(mask, np.inf, cr)
    cumulative, back = _accumulate_forward(cu, cl, cr, mask[0])
    path, _, ties = _backtrack(cumulative, back)
    return SeamResult(
        path=path,
        energy=float(cumulative[-1, path[-1]]),
        mode=ENERGY_FORWARD,
        max_displacement=1,
        start_col=path[0],
        final_row_ties=ties,
        diagnostics={
            "height": int(lum.shape[0]),
            "width": int(lum.shape[1]),
            "protected_pixels": int(mask.sum()),
        },
    )


def find_seam(
    image: np.ndarray,
    protected_mask: np.ndarray | None,
    settings: Settings,
) -> SeamResult:
    """Dispatch on the configured energy mode; computes energy from the image."""
    arr = np.asarray(image)
    if arr.ndim not in (2, 3):
        raise InvalidImageError(
            "image must be 2-D grayscale or 3-D RGB",
            details={"shape": list(arr.shape)},
        )
    if settings.energy_mode == ENERGY_GRADIENT:
        return find_seam_gradient(
            gradient_energy(arr), protected_mask, settings.max_displacement
        )
    if settings.energy_mode == ENERGY_FORWARD:
        return find_seam_forward(to_luminance(arr), protected_mask)
    raise InvalidRequestError(  # pragma: no cover - guarded by Settings.validate
        "unknown energy mode", details={"energy_mode": settings.energy_mode}
    )
