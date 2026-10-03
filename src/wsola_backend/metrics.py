"""Numeric quality metrics used by tests and the verification script.

These are independent of the stretch core: references here are closed-form
(least-squares sinusoid fit, FFT peak, peak-picking), not outputs of the
WSOLA implementation.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import find_peaks
from scipy.signal.windows import hann


def dominant_frequency(x: np.ndarray, sample_rate: int) -> float:
    """Peak of the windowed amplitude spectrum, parabola-refined, in Hz."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < 8:
        raise ValueError("need at least 8 samples")
    xc = x - x.mean()
    spectrum = np.abs(np.fft.rfft(xc * hann(x.size)))
    idx = int(np.argmax(spectrum))
    if 0 < idx < spectrum.size - 1:
        a, b, c = spectrum[idx - 1], spectrum[idx], spectrum[idx + 1]
        denom = a - 2.0 * b + c
        shift = 0.5 * (a - c) / denom if abs(denom) > 1e-30 else 0.0
    else:
        shift = 0.0
    return (idx + shift) * sample_rate / x.size


def seam_report(output: np.ndarray, join_positions: list[int]) -> dict:
    """Continuity at synthesis joins.

    Returns the worst absolute jump across each join, the 99th percentile of
    |diff| over the whole signal as a reference level, and their ratio.
    """
    out = np.asarray(output, dtype=np.float64)
    diffs = np.abs(np.diff(out))
    ref = float(np.percentile(diffs, 99)) if diffs.size else 0.0
    jumps = {
        int(j): float(abs(out[j] - out[j - 1]))
        for j in join_positions
        if 0 < j < out.size
    }
    worst = max(jumps.values(), default=0.0)
    return {
        "join_jumps": jumps,
        "worst_jump": worst,
        "reference_p99": ref,
        "worst_ratio": worst / ref if ref > 0 else 0.0,
    }


def sinusoid_residual_snr_db(x: np.ndarray, sample_rate: int, freq_hz: float) -> float:
    """SNR of `x` against the best-fit sinusoid at `freq_hz` (plus DC).

    The reference sinusoid comes from least squares on cos/sin basis vectors,
    not from the stretch core, so this is an independent distortion measure.
    """
    x = np.asarray(x, dtype=np.float64)
    t = np.arange(x.size) / sample_rate
    basis = np.column_stack([np.cos(2 * np.pi * freq_hz * t), np.sin(2 * np.pi * freq_hz * t), np.ones(x.size)])
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    residual = x - basis @ coef
    signal_power = float(np.sum((basis @ coef) ** 2))
    residual_power = float(np.sum(residual**2))
    if residual_power <= 0:
        return float("inf")
    return 10.0 * np.log10(signal_power / residual_power)


def impulse_spacings(x: np.ndarray, height_ratio: float = 0.5) -> np.ndarray:
    """Sample distances between successive impulses (peaks above height_ratio * max)."""
    x = np.asarray(x, dtype=np.float64)
    peak = float(np.max(np.abs(x))) if x.size else 0.0
    if peak <= 0:
        return np.empty(0, dtype=np.int64)
    peaks, _ = find_peaks(np.abs(x), height=height_ratio * peak)
    return np.diff(peaks)
