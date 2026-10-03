"""Independent measurement helpers for tests and the verification script.

These measure properties of the OUTPUT (dominant frequency, seam jumps,
sinusoid-fit SNR, impulse spacing). They never call the WSOLA core, so
they can serve as an independent reference in assertions.
"""
from __future__ import annotations

import numpy as np


def dominant_frequency(y: np.ndarray, sample_rate: int) -> float:
    """FFT peak with parabolic interpolation; DC excluded."""
    y = np.asarray(y, dtype=np.float64)
    n = y.shape[0]
    spectrum = np.abs(np.fft.rfft(y * np.hanning(n)))
    spectrum[0] = 0.0
    peak = int(np.argmax(spectrum))
    if 0 < peak < spectrum.shape[0] - 1:
        left, center, right = spectrum[peak - 1 : peak + 2]
        denominator = left - 2.0 * center + right
        shift = 0.5 * (left - right) / denominator if denominator != 0 else 0.0
    else:
        shift = 0.0
    return (peak + shift) * sample_rate / n


def max_seam_jump(y: np.ndarray, seam_positions: list[int]) -> float:
    """Max |y[p] - y[p-1]| over the given segment boundary positions."""
    jumps = [
        abs(float(y[p]) - float(y[p - 1]))
        for p in seam_positions
        if 0 < p < y.shape[0]
    ]
    return max(jumps, default=0.0)


def sinusoid_fit_snr_db(y: np.ndarray, freq_hz: float, sample_rate: int) -> float:
    """SNR against the best least-squares sinusoid at freq_hz.

    Independent reference: the reference sinusoid is fitted by ordinary
    least squares here, not produced by the stretch core.
    """
    y = np.asarray(y, dtype=np.float64)
    t = np.arange(y.shape[0]) / sample_rate
    design = np.column_stack(
        [np.sin(2.0 * np.pi * freq_hz * t), np.cos(2.0 * np.pi * freq_hz * t)]
    )
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coefficients
    residual = y - fitted
    signal_power = float(np.dot(fitted, fitted))
    noise_power = float(np.dot(residual, residual))
    if noise_power <= 0.0:
        return float("inf")
    if signal_power <= 0.0:
        return -float("inf")
    return 10.0 * np.log10(signal_power / noise_power)


def impulse_spacings(y: np.ndarray, threshold_ratio: float = 0.5) -> np.ndarray:
    """Distances between consecutive impulses above threshold_ratio * max."""
    y = np.asarray(y, dtype=np.float64)
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak <= 0.0:
        return np.zeros(0)
    positions = np.flatnonzero(np.abs(y) >= threshold_ratio * peak)
    return np.diff(positions)
