"""Energy functions.

Two deliberately separate energy definitions:

* :func:`gradient_energy` — ordinary gradient magnitude (Sobel on the
  luminance plane).  This is the classic Avidan-Shamir "e1" energy.
* :func:`forward_costs` — the forward-energy transition costs
  (CU/CL/CR) of Avidan & Shamir 2007, which price the *edge introduced
  by removing* a pixel rather than the gradient at the pixel itself.

The DP kernel consumes either a precomputed energy surface (gradient
mode) or per-step transition costs (forward mode); the two never share
an implementation path.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

_LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float64)


def to_luminance(image: np.ndarray) -> np.ndarray:
    """Return the float64 luminance plane of a grayscale or RGB image."""
    arr = np.asarray(image)
    if arr.ndim == 2:
        return arr.astype(np.float64)
    if arr.ndim == 3 and arr.shape[2] == 3:
        return arr.astype(np.float64) @ _LUMA_WEIGHTS
    raise ValueError(f"unsupported image shape {arr.shape}")


def gradient_energy(image: np.ndarray) -> np.ndarray:
    """Sobel gradient magnitude of the luminance plane (e1 energy)."""
    lum = to_luminance(image)
    gx = ndimage.sobel(lum, axis=1, mode="reflect")
    gy = ndimage.sobel(lum, axis=0, mode="reflect")
    return np.hypot(gx, gy)


def forward_costs(luminance: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Forward-energy transition costs for a vertical seam.

    Returns (CU, CL, CR) where each entry ``X[i, j]`` is the cost of the
    seam passing through pixel ``(i, j)`` arriving respectively straight
    from ``(i-1, j)``, diagonally from ``(i-1, j+1)`` (seam steps left),
    or diagonally from ``(i-1, j-1)`` (seam steps right)::

        CU(i,j) = |I(i, j+1) - I(i, j-1)|
        CL(i,j) = CU(i,j) + |I(i-1, j) - I(i, j-1)|
        CR(i,j) = CU(i,j) + |I(i-1, j) - I(i, j+1)|

    Horizontal neighbours are edge-clamped.  Row 0 costs are unused by
    the kernel (a seam's first row contributes no transition) but are
    returned fully populated for testability.
    """
    lum = np.asarray(luminance, dtype=np.float64)
    if lum.ndim != 2:
        raise ValueError("forward costs require a 2-D luminance plane")
    # Vectorised edge-clamped horizontal neighbours.
    left = lum[:, np.clip(np.arange(lum.shape[1]) - 1, 0, lum.shape[1] - 1)]
    right = lum[:, np.clip(np.arange(lum.shape[1]) + 1, 0, lum.shape[1] - 1)]
    cu = np.abs(right - left)
    prev_row = np.vstack([lum[:1], lum[:-1]])
    cl = cu + np.abs(prev_row - left)
    cr = cu + np.abs(prev_row - right)
    return cu, cl, cr
