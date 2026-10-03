"""Explicit time alignment between excitation and response.

The service never aligns implicitly: the caller either passes an explicit
integer delay or asks for a documented heuristic estimate.

Delay convention: a delay ``d >= 0`` means the response lags the excitation
by ``d`` samples, i.e. ``y[n] = (h * x)[n - d]``. Aligning therefore
*advances* the response by ``d`` samples (drops its first ``d`` samples and
truncates the excitation tail to keep lengths equal).
"""

from __future__ import annotations

import numpy as np
from scipy.signal import correlate, correlation_lags

from ..errors import InputError


def estimate_delay(excitation: np.ndarray, response: np.ndarray, max_delay: int) -> int:
    """Estimate the lag of ``response`` relative to ``excitation``.

    Uses the peak of the cross-correlation between response and excitation
    over lags ``[0, max_delay]``. This is exact for white excitation whose
    channel has its dominant tap at index 0; for colored excitation or
    channels with a late dominant tap it is a heuristic, which is why the
    chosen delay and its source are always reported in the run log.

    Raises:
        InputError: if signals are empty or max_delay is out of range.
    """
    x = np.asarray(excitation, dtype=float)
    y = np.asarray(response, dtype=float)
    n = x.size
    if n == 0 or y.size == 0:
        raise InputError("cannot estimate delay of empty signals", reason="empty_signal")
    if not 0 <= max_delay < n:
        raise InputError(
            "max_delay must satisfy 0 <= max_delay < len(excitation)",
            reason="max_delay_out_of_range",
            detail={"max_delay": max_delay, "n_samples": n},
        )
    corr = correlate(y, x, mode="full", method="fft")
    lags = correlation_lags(y.size, x.size, mode="full")
    window = (lags >= 0) & (lags <= max_delay)
    candidate_lags = lags[window]
    candidate_corr = np.abs(corr[window])
    return int(candidate_lags[int(np.argmax(candidate_corr))])


def apply_delay(
    excitation: np.ndarray, response: np.ndarray, delay: int
) -> tuple[np.ndarray, np.ndarray]:
    """Align signals for a response lag of ``delay`` samples.

    Returns ``(x_aligned, y_aligned)`` of equal length ``N - delay`` with
    ``y_aligned[i] = y[i + delay]``.
    """
    x = np.asarray(excitation, dtype=float)
    y = np.asarray(response, dtype=float)
    if x.size != y.size:
        raise InputError(
            "excitation and response must have equal length before alignment",
            reason="length_mismatch",
            detail={"n_excitation": int(x.size), "n_response": int(y.size)},
        )
    if not 0 <= delay < x.size:
        raise InputError(
            "delay must satisfy 0 <= delay < len(excitation)",
            reason="delay_out_of_range",
            detail={"delay": delay, "n_samples": int(x.size)},
        )
    if delay == 0:
        return x, y
    return x[: x.size - delay], y[delay:]
