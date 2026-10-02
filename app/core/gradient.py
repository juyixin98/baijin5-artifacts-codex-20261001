"""Gradient magnitude computation.

Turns a grayscale intensity image into the elevation (gradient) map that the
watershed flood consumes. Kept separate from the kernel so the kernel can
also be exercised directly on synthetic elevation fixtures.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage


def gradient_magnitude(image: np.ndarray, sigma: float = 0.0) -> np.ndarray:
    """Compute the Sobel gradient magnitude of a 2-D image.

    Args:
        image: 2-D array-like of intensities.
        sigma: optional Gaussian pre-smoothing standard deviation. ``0``
            disables smoothing. Smoothing is the standard remedy against
            over-segmentation on noisy gradients.

    Returns:
        ``float64`` array of the same shape with non-negative magnitudes.

    Raises:
        ValueError: if the input is not 2-D or sigma is negative.
    """
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"gradient input must be 2-D, got shape {arr.shape}")
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    if sigma > 0:
        arr = ndimage.gaussian_filter(arr, sigma=sigma)
    gx = ndimage.sobel(arr, axis=1, mode="nearest")
    gy = ndimage.sobel(arr, axis=0, mode="nearest")
    return np.hypot(gx, gy)
