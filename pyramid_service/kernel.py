"""Numeric kernels: exact 2x downsampling honoring the fixed center mapping.

Two kernels are provided:

* ``area`` — exact area (box) averaging.  Output pixel ``d`` is the mean of
  the input pixels whose centers fall in its footprint ``[2d, 2d+2)``; at odd
  edges the footprint is clipped and the mean renormalized over the available
  pixels.  This is an explicit anti-aliased (area) resample with scale
  exactly 2.
* ``gaussian`` — explicit anti-alias prefilter: a Gaussian blur
  (``scipy.ndimage.gaussian_filter``) followed by bilinear sampling at the
  fixed pixel centers ``2d + 0.5``.

Both map input shape ``(H, W, C)`` to ``(ceil(H/2), ceil(W/2), C)`` and keep
the declared pixel-center mapping of :mod:`pyramid_service.contracts`.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter, map_coordinates

from .errors import ComputeError, InputValidationError

GAUSSIAN_SIGMA = 0.6
# Input pixels needed beyond a tile boundary so the gaussian kernel matches a
# full-array application exactly (filter radius + interpolation support).
GAUSSIAN_MARGIN = 2 * int(np.ceil(4.0 * GAUSSIAN_SIGMA)) + 2

KERNEL_NAMES = ("area", "gaussian")


def _check_array(a: np.ndarray) -> None:
    if not isinstance(a, np.ndarray) or a.ndim != 3:
        raise ComputeError(
            f"kernel expects an (H, W, C) array, got shape "
            f"{getattr(a, 'shape', None)}"
        )
    if a.shape[0] < 1 or a.shape[1] < 1:
        raise ComputeError(f"kernel input must be non-empty, got {a.shape}")
    if not np.issubdtype(a.dtype, np.floating):
        raise ComputeError(f"kernel expects a floating dtype, got {a.dtype}")


def downsample_area_2x(a: np.ndarray) -> np.ndarray:
    """Exact area average over 2x2 blocks (clipped + renormalized at odd edges)."""
    _check_array(a)
    h, w, c = a.shape
    h2, w2 = (h + 1) // 2, (w + 1) // 2
    pad_h, pad_w = h2 * 2 - h, w2 * 2 - w
    padded = np.pad(a, ((0, pad_h), (0, pad_w), (0, 0)))
    coverage = np.pad(np.ones((h, w), dtype=np.float64), ((0, pad_h), (0, pad_w)))
    sums = padded.reshape(h2, 2, w2, 2, c).sum(axis=(1, 3))
    counts = coverage.reshape(h2, 2, w2, 2).sum(axis=(1, 3))
    return sums / counts[..., None]


def downsample_gaussian_2x(a: np.ndarray, sigma: float = GAUSSIAN_SIGMA) -> np.ndarray:
    """Gaussian prefilter + bilinear sampling at the fixed centers ``2d + 0.5``."""
    _check_array(a)
    h, w, c = a.shape
    h2, w2 = (h + 1) // 2, (w + 1) // 2
    blurred = gaussian_filter(a, sigma=(sigma, sigma, 0.0), mode="nearest")
    ys = np.arange(h2, dtype=np.float64) * 2.0 + 0.5
    xs = np.arange(w2, dtype=np.float64) * 2.0 + 0.5
    coords = np.meshgrid(ys, xs, indexing="ij")
    out = np.empty((h2, w2, c), dtype=np.float64)
    for ch in range(c):
        out[..., ch] = map_coordinates(
            blurred[..., ch], coords, order=1, mode="nearest"
        )
    return out


def kernel_margin(kernel: str) -> int:
    """Extra input pixels a tiled caller must supply around a target window."""
    if kernel == "area":
        return 0
    if kernel == "gaussian":
        return GAUSSIAN_MARGIN
    raise InputValidationError(
        f"unknown kernel {kernel!r}; expected one of {KERNEL_NAMES}"
    )


def downsample_2x(a: np.ndarray, kernel: str = "area") -> np.ndarray:
    """Dispatch to the named kernel."""
    if kernel == "area":
        return downsample_area_2x(a)
    if kernel == "gaussian":
        return downsample_gaussian_2x(a)
    raise InputValidationError(
        f"unknown kernel {kernel!r}; expected one of {KERNEL_NAMES}"
    )
