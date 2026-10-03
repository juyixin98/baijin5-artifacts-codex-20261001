"""Boundary resolution: map out-of-range sample indices to image content.

The three modes are defined by explicit index maps (see
docs/boundary-semantics.md for the full derivation):

* PERIODIC: ``f(i) = i mod n``
* MIRROR:   whole-sample symmetric reflection with period ``2n - 2``;
  ``f(i) = m`` if ``m < n`` else ``2n - 2 - m`` where ``m = i mod (2n - 2)``.
  The edge sample is not repeated: ``f(-1) = 1``, ``f(-2) = 2``.
  (Matches ``np.pad(..., "reflect")`` and scipy.ndimage mode "mirror".)
* CONSTANT: out-of-range samples are a constant ``cval``; there is no valid
  index, so :func:`extract_window` handles this mode by compositing.

Windows are extracted by index mapping against the *full* image, never by
padding a clipped crop — padding a crop would source periodic/mirror context
from the crop's interior instead of the image's far edge.
"""

from __future__ import annotations

import numpy as np

from .contract import BoundaryMode
from .errors import InvalidSpecError


def boundary_indices(idx: np.ndarray, n: int, mode: BoundaryMode) -> np.ndarray:
    """Map (possibly out-of-range) indices to valid indices in ``[0, n)``."""
    if n < 1:
        raise InvalidSpecError("cannot resolve boundary for empty axis", {"n": n})
    idx = np.asarray(idx, dtype=np.int64)
    if mode == BoundaryMode.PERIODIC:
        return np.mod(idx, n)
    if mode == BoundaryMode.MIRROR:
        if n == 1:
            return np.zeros_like(idx)
        period = 2 * n - 2
        m = np.mod(idx, period)
        return np.where(m < n, m, period - m)
    raise InvalidSpecError(
        "constant boundary has no in-range index; use extract_window",
        {"mode": str(mode)},
    )


def extract_window(
    img: np.ndarray,
    row0: int,
    row1: int,
    col0: int,
    col1: int,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Extract ``img[row0:row1, col0:col1]`` with out-of-range samples resolved.

    Bounds may extend past the image on any side (halo context). The result
    always has shape ``(row1 - row0, col1 - col0)`` and is a concrete ndarray
    (never a view), so callers may accumulate into it safely.
    """
    if row1 <= row0 or col1 <= col0:
        raise InvalidSpecError(
            "empty window requested",
            {"row0": row0, "row1": row1, "col0": col0, "col1": col1},
        )
    rows = np.arange(row0, row1, dtype=np.int64)
    cols = np.arange(col0, col1, dtype=np.int64)

    if mode == BoundaryMode.CONSTANT:
        out = np.full((rows.size, cols.size), cval, dtype=np.float64)
        r_lo, r_hi = max(row0, 0), min(row1, img.shape[0])
        c_lo, c_hi = max(col0, 0), min(col1, img.shape[1])
        if r_lo < r_hi and c_lo < c_hi:
            out[r_lo - row0 : r_hi - row0, c_lo - col0 : c_hi - col0] = img[r_lo:r_hi, c_lo:c_hi]
        return out

    ri = boundary_indices(rows, img.shape[0], mode)
    ci = boundary_indices(cols, img.shape[1], mode)
    return np.asarray(img[np.ix_(ri, ci)], dtype=np.float64)
