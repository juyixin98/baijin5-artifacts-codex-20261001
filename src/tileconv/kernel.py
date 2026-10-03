"""Numerical kernels.

Engine semantics: true convolution with explicit anchor (see contract.py)::

    out[i, j] = sum_{u,v} in[i - u + ar, j - v + ac] * K[u, v]

The internal primitive is a *valid correlation* shift-and-add; convolution
is obtained by correlating with the flipped kernel, with the halo computed
by ``KernelSpec.halo()`` (``n - 1 - a`` samples before, ``a`` after)::

    correlate_valid(P, K)[i, j] = sum_{u,v} P[i + u, j + v] * K[u, v]

A tile's read window already carries the boundary-resolved halo, so the valid
region of the correlation is exactly the tile's write region.

Reference path: :func:`direct_reference` delegates the full-image computation
to ``scipy.ndimage.convolve`` with *native* boundary modes. To keep anchor
semantics unambiguous, kernels are first zero-padded to odd size with the
anchor at the exact center (zero taps contribute nothing), so scipy's
default ``origin=0`` placement applies. This makes the reference independent
of the engine's padding/index-mapping code.
"""

from __future__ import annotations

import numpy as np

from .contract import BoundaryMode, KernelSpec
from .errors import InvalidSpecError
from .padding import extract_window

# Map our boundary modes to scipy.ndimage native modes (reference path only).
SCIPY_MODE_MAP = {
    BoundaryMode.MIRROR: "mirror",
    BoundaryMode.CONSTANT: "constant",
    BoundaryMode.PERIODIC: "wrap",
}


def correlate_valid(padded: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Valid-region correlation of ``padded`` with 2-D ``weights``.

    Output shape is ``(H - kh + 1, W - kw + 1)``; accumulation in float64.
    """
    padded = np.asarray(padded, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    kh, kw = weights.shape
    oh = padded.shape[0] - kh + 1
    ow = padded.shape[1] - kw + 1
    if oh < 1 or ow < 1:
        raise InvalidSpecError(
            "window smaller than kernel",
            {"window": list(padded.shape), "kernel": [kh, kw]},
        )
    out = np.zeros((oh, ow), dtype=np.float64)
    for u in range(kh):
        row = padded[u : u + oh]
        wu = weights[u]
        for v in range(kw):
            w = wu[v]
            if w != 0.0:
                out += w * row[:, v : v + ow]
    return out


def correlate1d_valid(arr: np.ndarray, weights: np.ndarray, axis: int) -> np.ndarray:
    """Valid-region 1-D correlation of ``arr`` along ``axis``."""
    arr = np.asarray(arr, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    n = weights.shape[0]
    out_len = arr.shape[axis] - n + 1
    if out_len < 1:
        raise InvalidSpecError(
            "window smaller than kernel along axis",
            {"window": list(arr.shape), "kernel": n, "axis": axis},
        )
    out_shape = list(arr.shape)
    out_shape[axis] = out_len
    out = np.zeros(out_shape, dtype=np.float64)
    for j in range(n):
        w = weights[j]
        if w == 0.0:
            continue
        sl = [slice(None)] * arr.ndim
        sl[axis] = slice(j, j + out_len)
        out += w * arr[tuple(sl)]
    return out


def filter_window(
    img: np.ndarray,
    row0: int,
    row1: int,
    col0: int,
    col1: int,
    kernel: KernelSpec,
    boundary: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Filter exactly the region ``[row0:row1) x [col0:col1)`` of ``img``.

    Reads the region expanded by the kernel halo (boundary-resolved) and
    returns the valid correlation, whose shape is ``(row1-row0, col1-col0)``.
    """
    top, bottom, left, right = kernel.halo()
    window = extract_window(img, row0 - top, row1 + bottom,
                            col0 - left, col1 + right, boundary, cval)
    if kernel.kind == "separable":
        # convolution == valid correlation with the flipped kernel
        tmp = correlate1d_valid(window, kernel.row_array()[::-1], axis=1)
        return correlate1d_valid(tmp, kernel.col_array()[::-1], axis=0)
    return correlate_valid(window, np.flip(kernel.dense_weights()))


def filter_full(
    img: np.ndarray,
    kernel: KernelSpec,
    boundary: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Engine full-image filter (pad whole image, then valid correlation)."""
    return filter_window(img, 0, img.shape[0], 0, img.shape[1], kernel, boundary, cval)


# --------------------------------------------------------------------- reference

def _center_kernel_1d(weights: np.ndarray, anchor: int) -> np.ndarray:
    """Zero-pad a 1-D kernel to odd length with the anchor at the center."""
    n = weights.shape[0]
    half = max(anchor, n - 1 - anchor)
    left = half - anchor
    right = half - (n - 1 - anchor)
    return np.pad(weights, (left, right), mode="constant")


def center_kernel_2d(weights: np.ndarray, anchor: tuple[int, int]) -> np.ndarray:
    """Zero-pad a 2-D kernel to odd shape with the anchor at the exact center."""
    pads = []
    for n, a in zip(weights.shape, anchor):
        half = max(a, n - 1 - a)
        pads.append((half - a, half - (n - 1 - a)))
    return np.pad(weights, pads, mode="constant")


def direct_reference(
    img: np.ndarray,
    kernel: KernelSpec,
    boundary: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Independent full-image reference via scipy.ndimage native boundary modes.

    No tiling and no manual padding: scipy resolves borders itself. Kernels
    are odd-centered first so the anchor placement is exact.
    """
    from scipy import ndimage

    mode = SCIPY_MODE_MAP[boundary]
    src = np.asarray(img, dtype=np.float64)
    if kernel.kind == "separable":
        col = _center_kernel_1d(kernel.col_array(), kernel.anchors_rc()[0])
        row = _center_kernel_1d(kernel.row_array(), kernel.anchors_rc()[1])
        tmp = ndimage.convolve1d(src, col, axis=0, mode=mode, cval=cval)
        return ndimage.convolve1d(tmp, row, axis=1, mode=mode, cval=cval)
    w = center_kernel_2d(kernel.dense_weights(), kernel.anchors_rc())
    return ndimage.convolve(src, w, mode=mode, cval=cval)
