"""Independent reference implementation of the limiter spec.

Written with plain Python loops directly from the documented formulas
(see src/limiter/config.py). It shares NO code with the package under
test, so cross-checking core vs reference detects implementation drift
in either direction. Sample-peak mode only.
"""

from __future__ import annotations

import numpy as np


def reference_limiter(
    pcm: np.ndarray,
    threshold: float,
    attack_coeff: float,
    release_coeff: float,
    lookahead: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Returns (delay-compensated output, gain trajectory aligned to input)."""
    pcm = np.asarray(pcm, dtype=np.float64)
    if pcm.ndim == 1:
        pcm = pcm[:, None]
    n = pcm.shape[0]

    # per-sample linked peak and required gain
    r = []
    for i in range(n):
        peak = max(abs(pcm[i, c]) for c in range(pcm.shape[1]))
        r.append(min(1.0, threshold / peak) if peak > 0.0 else 1.0)
    r_ext = r + [1.0] * lookahead

    # attack anticipation: g1[i] = min over j of r[i+j] * att**(-j)
    g1 = []
    for i in range(n):
        m = 1.0
        factor = 1.0
        for j in range(lookahead + 1):
            value = r_ext[i + j] * factor
            if value < m:
                m = value
            factor /= attack_coeff
        g1.append(m)

    # release smoothing: g[i] = min(g1[i], g[i-1] * rel)
    g = []
    prev = 1.0
    for i in range(n):
        recovered = prev * release_coeff
        prev = g1[i] if g1[i] < recovered else recovered
        g.append(prev)

    gain = np.asarray(g, dtype=np.float64)
    return pcm * gain[:, None], gain
