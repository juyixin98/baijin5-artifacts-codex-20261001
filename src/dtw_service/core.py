"""Core DTW accumulator: banded dynamic programming with slope-constrained states.

The dynamic program tracks, per cell, one state per "how did we get here":
diagonal, horizontal run of length r, vertical run of length r (r <= max_run).
Runs longer than max_run have no state, so the slope constraint is enforced
structurally rather than by post-hoc filtering.

Storage is banded: row ``i`` only materializes columns ``j`` with
``|i - j| <= window``, laid out at fixed offset ``idx = j - i + window``.
Memory is O(n * (2*window+1) * (1 + 2*max_run)) instead of O(n * m), which is
what allows large matrices. Costs roll over two rows; backpointers are kept
per cell as int8 (one byte per state per banded cell).

Normalization convention (fixed): ``normalized_cost = total_cost / len(path)``
where ``len(path)`` is the number of matched frame pairs. Comparisons between
alignments must use this normalized value, never raw totals of different
path lengths.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial.distance import cdist

from dtw_service.constraints import (
    DIAG_STATE,
    PathConstraints,
    horizontal_state,
    num_states,
    vertical_state,
)

_NO_PREDECESSOR = np.uint8(255)


class UnreachablePathError(Exception):
    """The endpoint (n-1, m-1) cannot be reached under the fixed constraints."""

    def __init__(self, n: int, m: int, constraints: PathConstraints, reason: str):
        self.n = n
        self.m = m
        self.constraints = constraints
        self.reason = reason
        super().__init__(
            f"endpoint ({n - 1}, {m - 1}) unreachable: {reason} "
            f"(window={constraints.window}, max_run={constraints.max_run})"
        )


@dataclass(frozen=True)
class DtwResult:
    path: list[tuple[int, int]]
    total_cost: float
    normalized_cost: float  # total_cost / len(path); fixed denominator convention
    path_length: int
    n: int
    m: int
    window: int
    max_run: int


def as_feature_matrix(sequence: np.ndarray | list[list[float]], name: str) -> np.ndarray:
    """Validate and coerce a sequence of feature frames to a 2-D float array."""
    arr = np.asarray(sequence, dtype=np.float64)
    if arr.ndim == 1 and arr.size > 0:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2 or arr.shape[0] == 0 or arr.shape[1] == 0:
        raise ValueError(f"{name} must be a non-empty 2-D sequence of frames")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} contains non-finite values")
    return arr


def local_distance_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Full local distance matrix under the fixed euclidean metric."""
    return cdist(a, b, metric="euclidean")


def _band_bounds(i: int, m: int, window: int) -> tuple[int, int]:
    return max(0, i - window), min(m - 1, i + window)


def dtw_align(
    query: np.ndarray | list[list[float]],
    reference: np.ndarray | list[list[float]],
    constraints: PathConstraints,
    keep_matrix: bool = False,
) -> DtwResult | tuple[DtwResult, np.ndarray]:
    """Align two feature sequences. Raises UnreachablePathError on failure."""
    a = as_feature_matrix(query, "query")
    b = as_feature_matrix(reference, "reference")
    if a.shape[1] != b.shape[1]:
        raise ValueError(
            f"feature dimensions differ: query has {a.shape[1]}, reference has {b.shape[1]}"
        )

    n, m = a.shape[0], b.shape[0]
    if not constraints.endpoint_possible(n, m):
        raise UnreachablePathError(n, m, constraints, "endpoint lies outside the Sakoe-Chiba band")

    window = constraints.window
    max_run = constraints.max_run
    width = 2 * window + 1
    states = num_states(max_run)

    prev = np.full((states, width), np.inf)
    cur = np.full((states, width), np.inf)
    backpointers = np.full((states, n, width), _NO_PREDECESSOR, dtype=np.uint8)
    dense = np.full((n, m), np.inf) if keep_matrix else None

    for i in range(n):
        j_lo, j_hi = _band_bounds(i, m, window)
        band = b[j_lo : j_hi + 1]
        local = np.linalg.norm(a[i][None, :] - band, axis=1)
        idx_lo = j_lo - i + window  # band offset of j_lo in this row's layout

        cur.fill(np.inf)
        for offset in range(local.shape[0]):
            idx = idx_lo + offset
            j = i - window + idx
            d = local[offset]

            # Diagonal arrival: any predecessor state at (i-1, j-1) -> same idx.
            if i > 0 and j > 0:
                prev_col = prev[:, idx]
                best = int(np.argmin(prev_col))
                if np.isfinite(prev_col[best]):
                    cur[DIAG_STATE, idx] = d + prev_col[best]
                    backpointers[DIAG_STATE, i, idx] = best
            elif i == 0 and j == 0:
                cur[DIAG_STATE, idx] = d

            # Horizontal arrival from (i, j-1) -> idx - 1 in the current row.
            if j > 0 and idx - 1 >= 0:
                fresh = [DIAG_STATE] + [
                    vertical_state(r, max_run) for r in range(1, max_run + 1)
                ]
                pool = cur[fresh, idx - 1]
                best = int(np.argmin(pool))
                if np.isfinite(pool[best]):
                    cur[horizontal_state(1), idx] = d + pool[best]
                    backpointers[horizontal_state(1), i, idx] = fresh[best]
                for r in range(2, max_run + 1):
                    carried = cur[horizontal_state(r - 1), idx - 1]
                    if np.isfinite(carried):
                        cur[horizontal_state(r), idx] = d + carried
                        backpointers[horizontal_state(r), i, idx] = horizontal_state(r - 1)

            # Vertical arrival from (i-1, j) -> idx + 1 in the previous row.
            if i > 0 and idx + 1 < width:
                fresh = [DIAG_STATE] + [horizontal_state(r) for r in range(1, max_run + 1)]
                pool = prev[fresh, idx + 1]
                best = int(np.argmin(pool))
                if np.isfinite(pool[best]):
                    cur[vertical_state(1, max_run), idx] = d + pool[best]
                    backpointers[vertical_state(1, max_run), i, idx] = fresh[best]
                for r in range(2, max_run + 1):
                    carried = prev[vertical_state(r - 1, max_run), idx + 1]
                    if np.isfinite(carried):
                        cur[vertical_state(r, max_run), idx] = d + carried
                        backpointers[vertical_state(r, max_run), i, idx] = vertical_state(
                            r - 1, max_run
                        )

        if dense is not None:
            row_min = cur.min(axis=0)
            dense[i, j_lo : j_hi + 1] = row_min[idx_lo : idx_lo + local.shape[0]]
        prev, cur = cur, prev

    idx_end = (m - 1) - (n - 1) + window
    end_col = prev[:, idx_end]
    end_state = int(np.argmin(end_col))
    total = float(end_col[end_state])
    if not np.isfinite(total):
        raise UnreachablePathError(
            n, m, constraints, "no legal step sequence reaches the endpoint"
        )

    path = _backtrack(backpointers, n, m, window, max_run, end_state)
    result = DtwResult(
        path=path,
        total_cost=total,
        normalized_cost=total / len(path),
        path_length=len(path),
        n=n,
        m=m,
        window=window,
        max_run=max_run,
    )
    if dense is not None:
        return result, dense
    return result


def _backtrack(
    backpointers: np.ndarray,
    n: int,
    m: int,
    window: int,
    max_run: int,
    end_state: int,
) -> list[tuple[int, int]]:
    path: list[tuple[int, int]] = []
    i, j, state = n - 1, m - 1, end_state
    while True:
        path.append((i, j))
        if i == 0 and j == 0:
            break
        idx = j - i + window
        predecessor = int(backpointers[state, i, idx])
        if predecessor == int(_NO_PREDECESSOR):
            raise UnreachablePathError(
                n,
                m,
                PathConstraints(window=window, max_run=max_run),
                f"backtracking broke at cell ({i}, {j})",
            )
        if state == DIAG_STATE:
            i, j = i - 1, j - 1
        elif state <= max_run:  # horizontal run: came from (i, j-1)
            j = j - 1
        else:  # vertical run: came from (i-1, j)
            i = i - 1
        state = predecessor
    path.reverse()
    return path
