"""Energy functions.

Two energy definitions are kept deliberately separate (acceptance rule 1):

* ``gradient_energy`` -- ordinary backward/forward gradient magnitude:
  central differences with clamped borders, |dx| + |dy| per pixel.
* ``forward_energy_components`` -- Avidan & Shamir forward energy, returned
  as the three directional cost matrices (CU, CL, CR) the DP consumes.

For RGB images each channel contributes additively (sum over channels).
All energy arrays are float64, shape HxW.
"""

from __future__ import annotations

import numpy as np

from .errors import ContractViolationError


def _check_image(image: np.ndarray) -> np.ndarray:
    if not isinstance(image, np.ndarray) or image.ndim not in (2, 3):
        raise ContractViolationError("energy expects an HxW or HxWx3 array")
    if image.ndim == 3 and image.shape[2] != 3:
        raise ContractViolationError("RGB images must have exactly 3 channels")
    return image.astype(np.float64)


def _clamp_diff(g: np.ndarray, axis: int) -> np.ndarray:
    """Central difference |g[i+1] - g[i-1]| along `axis` with clamped borders."""
    n = g.shape[axis]
    idx = np.arange(n)
    hi = np.take(g, np.clip(idx + 1, 0, n - 1), axis=axis)
    lo = np.take(g, np.clip(idx - 1, 0, n - 1), axis=axis)
    return np.abs(hi - lo)


def _channels(image: np.ndarray) -> list[np.ndarray]:
    f = _check_image(image)
    if f.ndim == 2:
        return [f]
    return [f[:, :, c] for c in range(3)]


def gradient_energy(image: np.ndarray) -> np.ndarray:
    """Ordinary gradient energy: |dx| + |dy|, summed over channels."""
    energy = None
    for g in _channels(image):
        e = _clamp_diff(g, axis=1) + _clamp_diff(g, axis=0)
        energy = e if energy is None else energy + e
    return energy


def forward_energy_components(
    image: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Forward energy cost matrices (CU, CL, CR), summed over channels.

    For pixel (i, j) on grayscale g (border indices clamped):
        CU = |g[i, j+1]   - g[i, j-1]|
        CL = CU + |g[i-1, j] - g[i, j-1]|
        CR = CU + |g[i, j+1] - g[i-1, j]|
    """
    cu = cl = cr = None
    for g in _channels(image):
        cu_c = _clamp_diff(g, axis=1)
        up = np.take(g, np.clip(np.arange(g.shape[0]) - 1, 0, g.shape[0] - 1), axis=0)
        left = np.take(g, np.clip(np.arange(g.shape[1]) - 1, 0, g.shape[1] - 1), axis=1)
        right = np.take(g, np.clip(np.arange(g.shape[1]) + 1, 0, g.shape[1] - 1), axis=1)
        cl_c = cu_c + np.abs(up - left)
        cr_c = cu_c + np.abs(right - up)
        if cu is None:
            cu, cl, cr = cu_c, cl_c, cr_c
        else:
            cu, cl, cr = cu + cu_c, cl + cl_c, cr + cr_c
    return cu, cl, cr
