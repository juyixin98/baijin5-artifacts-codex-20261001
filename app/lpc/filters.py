"""Analysis and synthesis filters with explicit, corresponding states.

Analysis (FIR):   e[n] = x[n] + sum_{i=1..p} a[i] * x[n-i]
Synthesis (IIR):  y[n] = e[n] - sum_{i=1..p} a[i] * y[n-i]

The two filters are exact inverses when their initial states
*correspond*: both must describe the same signal history. We make the
correspondence explicit by deriving each filter's transposed-Direct-
Form-II state (scipy.signal.lfilter's zi convention) in closed form
from raw sample history:

  - analysis state   <- the `order` previous *input* samples:
                        zi[i] = sum_{j=i+1..p} a[j] * x[N-1-(j-i-1)]
  - synthesis state  <- the `order` previous *output* samples:
                        zi[i] = -sum_{j=1..p-i} a[i+j] * y[N-j]
                        (an all-pole filter's state is fully determined
                        by its past outputs)

Zero history on both sides is the canonical corresponding pair and
yields exact round-trips up to floating-point rounding. The tests
assert these closed forms equal lfilter's own final states (zf).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import lfilter


def _validate_lpc(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64)
    if a.ndim != 1 or a.size < 2:
        raise ValueError("lpc must be a 1-D vector [1, a1, ..., a_order]")
    if a[0] != 1.0:
        raise ValueError("lpc[0] must be exactly 1.0 (monic predictor)")
    return a


def _padded_tail(samples: np.ndarray, order: int) -> np.ndarray:
    """Last `order` samples, left-padded with zeros, most recent last."""
    tail = np.asarray(samples, dtype=np.float64)[-order:]
    t = np.zeros(order)
    if tail.size:
        t[-tail.size :] = tail
    return t


def analysis_state_from_history(prev_samples: np.ndarray, a: np.ndarray) -> np.ndarray:
    """lfilter zi for the FIR analysis filter A(z), from past inputs."""
    a = _validate_lpc(a)
    p = a.size - 1
    t = _padded_tail(prev_samples, p)
    zi = np.zeros(p)
    for i in range(p):
        j = np.arange(i + 1, p + 1)
        zi[i] = float(np.dot(a[j], t[p - j + i]))
    return zi


def synthesis_state_from_history(prev_output: np.ndarray, a: np.ndarray) -> np.ndarray:
    """lfilter zi for the IIR synthesis filter 1/A(z), from past outputs."""
    a = _validate_lpc(a)
    p = a.size - 1
    t = _padded_tail(prev_output, p)
    zi = np.zeros(p)
    for i in range(p):
        j = np.arange(1, p - i + 1)
        zi[i] = -float(np.dot(a[i + j], t[p - j]))
    return zi


def analysis_filter(
    x: np.ndarray, a: np.ndarray, zi: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Filter a frame through A(z); returns (residual, final_state)."""
    a = _validate_lpc(a)
    x = np.asarray(x, dtype=np.float64)
    if zi is None:
        zi = np.zeros(a.size - 1)
    residual, zf = lfilter(a, [1.0], x, zi=zi)
    return residual, zf


def synthesis_filter(
    residual: np.ndarray, a: np.ndarray, zi: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Filter a residual frame through 1/A(z); returns (signal, final_state)."""
    a = _validate_lpc(a)
    residual = np.asarray(residual, dtype=np.float64)
    if zi is None:
        zi = np.zeros(a.size - 1)
    y, zf = lfilter([1.0], a, residual, zi=zi)
    return y, zf
