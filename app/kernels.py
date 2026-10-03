"""Numerical kernels: boundary extension, dense filtering, separable filtering.

The core operator (see :mod:`app.contract` for the anchor convention) is::

    out[y, x] = sum_{i,j} w[i, j] * ext[y + i - ay, x + j - ax]

where ``ext`` is the boundary-extended source image.  All routines work on an
arbitrary output window ``(y0, x0, H, W)`` so the tiling layer can evaluate
exactly the pixels it owns without materialising a padded full image.
"""
from __future__ import annotations

import numpy as np

from .contract import BoundaryMode, KernelSpec, SeparableKernelSpec


def boundary_indices(
    n: int, start: int, stop: int, mode: BoundaryMode
) -> tuple[np.ndarray, np.ndarray | None]:
    """Map extended coordinates ``[start, stop)`` to source row/col indices.

    Returns ``(indices, const_mask)``.  ``const_mask`` is ``None`` unless the
    mode is ``CONSTANT``; where it is True the coordinate falls outside the
    source and the caller must substitute the constant value.
    """
    if n < 1:
        raise ValueError("source extent must be >= 1")
    pos = np.arange(start, stop, dtype=np.int64)
    if mode is BoundaryMode.PERIODIC:
        return pos % n, None
    if mode is BoundaryMode.MIRROR:
        # Half-sample symmetric: period 2n, edge sample repeated.
        # n=3, pos -4..5 -> 2 2 1 0 0 1 2 2 1 0  (asserted verbatim in tests)
        if n == 1:
            return np.zeros_like(pos), None
        period = 2 * n
        m = pos % period
        m = np.where(m >= n, period - 1 - m, m)
        return m, None
    if mode is BoundaryMode.CONSTANT:
        inside = (pos >= 0) & (pos < n)
        return np.clip(pos, 0, n - 1), ~inside
    raise ValueError(f"unsupported boundary mode: {mode!r}")


def extract_region(
    img: np.ndarray,
    r0: int,
    r1: int,
    c0: int,
    c1: int,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Extract ``ext[r0:r1, c0:c1]`` from the boundary-extended image.

    Only the requested window is materialised, which is what keeps tiled
    processing memory-bounded.  Coordinates may be negative or exceed the
    image extent; they are resolved per the boundary mode.
    """
    if r1 <= r0 or c1 <= c0:
        raise ValueError(f"empty region rows [{r0},{r1}) cols [{c0},{c1})")
    rows, rconst = boundary_indices(img.shape[0], r0, r1, mode)
    cols, cconst = boundary_indices(img.shape[1], c0, c1, mode)
    region = img[np.ix_(rows, cols)]  # fancy indexing -> already a new array
    if rconst is not None or cconst is not None:
        if rconst is not None:
            region[rconst, :] = cval
        if cconst is not None:
            region[:, cconst] = cval
    return region


def _accumulate(region: np.ndarray, weights: np.ndarray, out_shape: tuple[int, int]) -> np.ndarray:
    """out[y, x] = sum_{i,j} w[i,j] * region[y+i, x+j]  (zero weights skipped)."""
    ky, kx = weights.shape
    H, W = out_shape
    if region.shape != (H + ky - 1, W + kx - 1):
        raise ValueError(
            f"region shape {region.shape} incompatible with out {out_shape} "
            f"and kernel {weights.shape}"
        )
    out = np.zeros((H, W), dtype=np.float64)
    for i in range(ky):
        for j in range(kx):
            w = weights[i, j]
            if w != 0.0:
                out += w * region[i : i + H, j : j + W]
    return out


def filter_window(
    img: np.ndarray,
    spec: KernelSpec,
    origin: tuple[int, int],
    out_shape: tuple[int, int],
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Evaluate the dense filter on output window ``origin + out_shape``."""
    y0, x0 = origin
    H, W = out_shape
    top, bottom, left, right = spec.halo
    region = extract_region(
        img, y0 - top, y0 + H + bottom, x0 - left, x0 + W + right, mode, cval
    )
    return _accumulate(region, spec.array, (H, W))


def filter_separable_window(
    img: np.ndarray,
    spec: SeparableKernelSpec,
    origin: tuple[int, int],
    out_shape: tuple[int, int],
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Evaluate a separable filter on an output window via two 1-D passes.

    The horizontal pass runs on the row-extended region (vertical halo kept,
    horizontal halo consumed); the vertical pass then consumes the vertical
    halo.  Peak extra memory is one halo-extended tile plus one row-buffer.
    """
    y0, x0 = origin
    H, W = out_shape
    top, bottom, left, right = spec.halo
    ky, kx = spec.shape
    region = extract_region(
        img, y0 - top, y0 + H + bottom, x0 - left, x0 + W + right, mode, cval
    )
    row_w = np.asarray(spec.row, dtype=np.float64).reshape(1, kx)
    hpass = _accumulate(region, row_w, (H + ky - 1, W))
    col_w = np.asarray(spec.col, dtype=np.float64).reshape(ky, 1)
    return _accumulate(hpass, col_w, (H, W))


def filter_direct(
    img: np.ndarray,
    spec: KernelSpec,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Dense filter over the whole image in one pass."""
    return filter_window(img, spec, (0, 0), img.shape, mode, cval)


def filter_separable_direct(
    img: np.ndarray,
    spec: SeparableKernelSpec,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Separable filter over the whole image in one pass."""
    return filter_separable_window(img, spec, (0, 0), img.shape, mode, cval)
