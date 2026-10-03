"""Independent naive reference implementation, owned by the test suite.

Deliberately written in a different style from the production kernel
(per-pixel Python loops, no shared helpers) so that a bug in the optimized
kernels cannot be "confirmed" by a copy of itself. Returns the result and the
number of dilation passes executed, including the final stable pass — the
same counting convention as ``kernel.reconstruct_sync``.
"""
from __future__ import annotations

import numpy as np

OFFSETS = {
    4: ((-1, 0), (1, 0), (0, -1), (0, 1)),
    8: (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1), (0, 1),
        (1, -1), (1, 0), (1, 1),
    ),
}


def naive_reconstruct(
    marker: np.ndarray, mask: np.ndarray, connectivity: int = 4, max_passes: int = 100_000
) -> tuple[np.ndarray, int]:
    rec = np.array(marker, dtype=float, copy=True)
    mask = np.asarray(mask, dtype=float)
    h, w = rec.shape
    offsets = OFFSETS[connectivity]
    for pass_no in range(1, max_passes + 1):
        new = rec.copy()
        for y in range(h):
            for x in range(w):
                best = rec[y, x]
                for dy, dx in offsets:
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w:  # edge-ignore boundary rule
                        if rec[ny, nx] > best:
                            best = rec[ny, nx]
                grown = best if best < mask[y, x] else mask[y, x]
                if grown > rec[y, x]:
                    new[y, x] = grown
        if np.array_equal(new, rec):
            return new, pass_no
        rec = new
    raise AssertionError("naive reference did not converge")
