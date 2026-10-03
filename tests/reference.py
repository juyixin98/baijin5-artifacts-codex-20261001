"""Independent reference implementations used ONLY by the test-suite.

The core under test (app.dsp.cascade) uses transposed Direct-Form II with
vectorized-per-channel state. These references are deliberately different
code paths so agreement is meaningful:

- ``reference_sos_filter``: plain-Python Direct-Form I difference
  equations, sample by sample, channel by channel.
- scipy.signal.sosfilt: used directly in tests as a second reference.
"""

from __future__ import annotations

import numpy as np


def reference_sos_filter(sos: np.ndarray, x: np.ndarray) -> np.ndarray:
    """Direct-Form I cascade, zero initial state.

    Args:
        sos: (n_sections, 6) normalized rows [b0,b1,b2,1,a1,a2].
        x: (n_samples, n_channels) input.
    Returns:
        (n_samples, n_channels) filtered output.
    """
    sos = np.asarray(sos, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    n_samples, n_channels = x.shape
    sig = [list(map(float, x[:, c])) for c in range(n_channels)]
    for row in sos:
        b0, b1, b2, _, a1, a2 = (float(v) for v in row)
        for c in range(n_channels):
            s = sig[c]
            y = [0.0] * n_samples
            xm1 = xm2 = ym1 = ym2 = 0.0
            for n in range(n_samples):
                xn = s[n]
                yn = b0 * xn + b1 * xm1 + b2 * xm2 - a1 * ym1 - a2 * ym2
                y[n] = yn
                xm2, xm1 = xm1, xn
                ym2, ym1 = ym1, yn
            sig[c] = y
    return np.array([[sig[c][n] for c in range(n_channels)]
                     for n in range(n_samples)])


def dc_gain(sos: np.ndarray) -> float:
    """Analytic DC gain of the cascade: prod(sum(b_k)/sum(a_k))."""
    gain = 1.0
    for row in np.asarray(sos, dtype=np.float64):
        b0, b1, b2, _, a1, a2 = row
        gain *= (b0 + b1 + b2) / (1.0 + a1 + a2)
    return float(gain)
