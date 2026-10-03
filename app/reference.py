"""Independent reference implementations used by validation and tests.

These deliberately do NOT call :mod:`app.kernels`.  ``scipy_reference`` is
built on ``scipy.ndimage.correlate``; ``naive_reference`` is a literal loop
over the contract formula.  Tests cross-check all three (tiled core, scipy,
naive) so the answer is never generated solely by the code under test.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from .contract import BoundaryMode, KernelSpec, SeparableKernelSpec

_NDIMAGE_MODE = {
    BoundaryMode.MIRROR: "reflect",  # half-sample symmetric, edge repeated
    BoundaryMode.CONSTANT: "constant",
    BoundaryMode.PERIODIC: "wrap",
}


def _odd_padded(weights: np.ndarray, anchor: tuple[int, int]) -> np.ndarray:
    """Pad an even-sized kernel with zeros to odd size, preserving offsets.

    Kernel offsets are ``i - anchor``.  Appending a zero row/column at the
    end extends the offset range by +1 on that axis with weight 0, which
    leaves the result unchanged but makes the kernel odd-sized so the
    ndimage centre convention is unambiguous.
    """
    ky, kx = weights.shape
    ay, ax = anchor
    pad_y = (ky % 2 == 0)
    pad_x = (kx % 2 == 0)
    if not (pad_y or pad_x):
        return weights
    return np.pad(weights, ((0, int(pad_y)), (0, int(pad_x))))


def _odd_padded_1d(vec: np.ndarray) -> np.ndarray:
    """Pad an even-length 1-D kernel with a trailing zero (offset-preserving)."""
    if vec.size % 2 == 1:
        return vec
    return np.concatenate([vec, [0.0]])


def scipy_reference(
    img: np.ndarray,
    spec: KernelSpec | SeparableKernelSpec,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Whole-image reference via scipy.ndimage (independent of app.kernels)."""
    nd_mode = _NDIMAGE_MODE[mode]
    if isinstance(spec, SeparableKernelSpec):
        # Zero-pad even-length vectors to odd so the ndimage centre
        # convention is unambiguous; the zero weight changes nothing.
        row = _odd_padded_1d(np.asarray(spec.row, dtype=np.float64))
        col = _odd_padded_1d(np.asarray(spec.col, dtype=np.float64))
        # Anchor after padding: original anchor (offsets i - anchor kept,
        # one extra zero-weight offset appended at the far end).
        return ndimage.correlate1d(
            ndimage.correlate1d(
                img, row, axis=1, mode=nd_mode, cval=cval,
                origin=spec.anchor_x - (row.size - 1) // 2,
            ),
            col, axis=0, mode=nd_mode, cval=cval,
            origin=spec.anchor_y - (col.size - 1) // 2,
        )
    weights = _odd_padded(spec.array, spec.anchor)
    py, px = weights.shape
    return ndimage.correlate(
        img,
        weights,
        mode=nd_mode,
        cval=cval,
        origin=(spec.anchor_y - (py - 1) // 2, spec.anchor_x - (px - 1) // 2),
    )


def naive_reference(
    img: np.ndarray,
    spec: KernelSpec,
    mode: BoundaryMode,
    cval: float = 0.0,
) -> np.ndarray:
    """Literal loop over the contract formula.  Slow; for small images."""
    H, W = img.shape
    w = spec.array
    ky, kx = w.shape
    ay, ax = spec.anchor
    out = np.zeros((H, W), dtype=np.float64)

    def sample(y: int, x: int) -> float:
        if mode is BoundaryMode.PERIODIC:
            return img[y % H, x % W]
        if mode is BoundaryMode.MIRROR:
            if H > 1:
                y %= 2 * H
                if y >= H:
                    y = 2 * H - 1 - y
            else:
                y = 0
            if W > 1:
                x %= 2 * W
                if x >= W:
                    x = 2 * W - 1 - x
            else:
                x = 0
            return img[y, x]
        # CONSTANT
        if 0 <= y < H and 0 <= x < W:
            return img[y, x]
        return cval

    for y in range(H):
        for x in range(W):
            acc = 0.0
            for i in range(ky):
                for j in range(kx):
                    if w[i, j] != 0.0:
                        acc += w[i, j] * sample(y + i - ay, x + j - ax)
            out[y, x] = acc
    return out
