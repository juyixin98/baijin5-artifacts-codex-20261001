"""Explicit time alignment between excitation and response.

Delay convention: a positive integer delay d means the response lags the
excitation by d samples, i.e. response[t] ~= (x * h)[t - d]. Alignment
removes the first d response samples (and the matching excitation tail)
so that aligned index 0 of both signals refers to the same instant.

Delay is an explicit parameter of the estimation request. `estimate_delay`
is provided only as a diagnostic aid (cross-correlation peak) so callers
can discover a plausible value before stating it explicitly.
"""

from __future__ import annotations

import numpy as np

from .contracts import SampleBlock
from .errors import InputValidationError


def align_pair(block: SampleBlock, delay: int) -> SampleBlock:
    """Return a new SampleBlock with the integer delay removed.

    Positive delay (response lags): drop the first `delay` response
    samples and the last `delay` excitation samples.
    Negative delay (response leads): symmetric opposite.
    """
    n = block.n_samples
    d = int(delay)
    if abs(d) >= n:
        raise InputValidationError(
            "delay magnitude must be smaller than the sample count",
            detail={"delay": d, "n_samples": n},
        )
    if d > 0:
        return SampleBlock(excitation=block.excitation[: n - d], response=block.response[d:])
    if d < 0:
        return SampleBlock(excitation=block.excitation[-d:], response=block.response[: n + d])
    return block


def estimate_delay(
    excitation: np.ndarray,
    response: np.ndarray,
    *,
    max_delay: int = 256,
) -> int:
    """Diagnostic: lag of the cross-correlation peak within +/- max_delay.

    Returns the delay in the same sign convention as `align_pair`:
    a positive value means the response lags the excitation.
    """
    x = np.asarray(excitation, dtype=float)
    y = np.asarray(response, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size == 0 or y.size == 0:
        raise InputValidationError("excitation and response must be non-empty 1-D arrays")
    if max_delay < 0:
        raise InputValidationError("max_delay must be >= 0")
    # Correlate response against excitation; lag k means y[t] ~ x[t - k].
    corr = np.correlate(y - y.mean(), x - x.mean(), mode="full")
    lags = np.arange(-(x.size - 1), y.size)
    limit = min(max_delay, x.size - 1, y.size - 1)
    window = (lags >= -limit) & (lags <= limit)
    best = int(lags[window][np.argmax(corr[window])])
    return best
