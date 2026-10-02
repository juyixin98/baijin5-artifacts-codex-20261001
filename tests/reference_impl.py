"""Independent reference implementation used only by the tests.

Deliberately shares no code with ``app.kernel``: integer shifts are found by
an explicit spatial-domain search of the zero-mean normalised cross-correlation
over a bounded range (direct loops, no FFT).  Ground truth for sub-pixel cases
comes from the fixture manifest (generation parameters), not from any
estimator.
"""
from __future__ import annotations

import numpy as np


def best_integer_shift_ncc(reference: np.ndarray, moving: np.ndarray,
                           max_shift: int) -> tuple[tuple[int, int], float]:
    """Brute-force NCC over all integer shifts in [-max_shift, max_shift].

    Returns ``((dy, dx), ncc)`` for the best overlap-constrained match, using
    the same sign convention as the kernel: moving[y, x] == ref[y - dy, x - dx].
    """
    h, w = reference.shape
    best_shift = (0, 0)
    best_ncc = -2.0
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            y0, y1 = max(0, -dy), min(h, h - dy)
            x0, x1 = max(0, -dx), min(w, w - dx)
            if y1 - y0 < 8 or x1 - x0 < 8:
                continue
            a = reference[y0:y1, x0:x1].astype(np.float64)
            b = moving[y0 + dy:y1 + dy, x0 + dx:x1 + dx].astype(np.float64)
            av = a.ravel() - a.mean()
            bv = b.ravel() - b.mean()
            denom = np.linalg.norm(av) * np.linalg.norm(bv)
            if denom == 0.0:
                continue
            ncc = float(av @ bv / denom)
            if ncc > best_ncc:
                best_ncc = ncc
                best_shift = (dy, dx)
    return best_shift, best_ncc
