"""Naive synchronous-iteration reference kernel.

This is the specification made executable: one full-image geodesic
dilation step per iteration, repeated until nothing changes.  It is
deliberately simple (and slow) so it can serve as the ground truth the
queue and tiled engines are checked against.  It is implemented
independently of those engines, on top of scipy's grey_dilation.
"""

from __future__ import annotations

import numpy as np
from scipy.ndimage import generate_binary_structure, grey_dilation

from ..schemas import Connectivity
from .queue_impl import KernelStats


def _footprint(connectivity: Connectivity) -> np.ndarray:
    # generate_binary_structure(2, 1) -> 4-connected cross;
    # generate_binary_structure(2, 2) -> 8-connected square.
    rank = 1 if connectivity == Connectivity.FOUR else 2
    return generate_binary_structure(2, rank)


def reconstruct_reference(
    marker: np.ndarray,
    mask: np.ndarray,
    connectivity: Connectivity = Connectivity.EIGHT,
    *,
    max_iterations: int = 0,
) -> tuple[np.ndarray, KernelStats]:
    """Iterate min(dilate(x), mask) synchronously to the fixpoint.

    Returns (result, stats) where stats.iterations counts the dilation
    steps performed (including the final no-change verification step)
    and stats.changed_pixels is the size of the last non-empty update.
    """
    if marker.shape != mask.shape:
        raise ValueError("marker and mask must have the same shape")
    footprint = _footprint(connectivity)

    current = marker.copy()
    iterations = 0
    changed = 0
    while True:
        dilated = grey_dilation(current, footprint=footprint, mode="constant", cval=0)
        nxt = np.minimum(dilated, mask)
        iterations += 1
        diff = nxt != current
        if not diff.any():
            return current, KernelStats(
                iterations=iterations, changed_pixels=changed
            )
        changed = int(np.count_nonzero(diff))
        current = nxt
        if max_iterations and iterations >= max_iterations:
            raise RuntimeError(
                f"reference iteration did not converge within "
                f"{max_iterations} steps"
            )
