"""Sub-pixel peak refinement and confidence estimation.

Two independent refinements are computed and cross-checked:

* ``refine_dft`` evaluates the exact inverse DFT of the normalised cross-power
  spectrum on a fine grid around the integer peak (matrix-multiply DFT in the
  spirit of Guizar-Sicairos et al., 2008, but written as a direct evaluation so
  the transform convention is defined by this file alone).
* ``refine_parabolic`` fits a 1-D parabola per axis on the correlation surface.

Their disagreement (in pixels) is reported as part of the confidence block;
large disagreement is a failure signal, not something to average away.
"""
from __future__ import annotations

import numpy as np


def eval_surface_at(normalized_cross_power: np.ndarray, y: float, x: float) -> float:
    """Exact value of ifft2(R) at fractional (y, x), in wrapped coordinates.

    Band-limited (Dirichlet) interpolation of the correlation surface:
    ``r[y, x] = 1/(H*W) * sum_{u,v} R[u, v] * exp(2j*pi*(u*y/H + v*x/W))``
    where u, v are *centered* DFT frequencies.  Using ``arange`` instead of
    centered frequencies agrees at integer coordinates but evaluates an
    aliased function at fractional ones — this distinction is load-bearing.
    """
    H, W = normalized_cross_power.shape
    u = np.fft.fftfreq(H) * H
    v = np.fft.fftfreq(W) * W
    ey = np.exp(2j * np.pi * u * y / H)
    ex = np.exp(2j * np.pi * v * x / W)
    return float(np.real(ey @ normalized_cross_power @ ex) / (H * W))


def refine_dft(normalized_cross_power: np.ndarray,
               peak_index: tuple[int, int],
               rounds: tuple = ((3, 0.1), (3, 0.01))) -> tuple[float, float, float]:
    """Grid-refine the peak position in wrapped (index) coordinates.

    Each round evaluates a ``grid x grid`` neighbourhood with spacing ``step``
    around the current best position.  Returns ``(y, x, value)``.
    """
    cy, cx = float(peak_index[0]), float(peak_index[1])
    best_value = eval_surface_at(normalized_cross_power, cy, cx)
    for grid, step in rounds:
        half = grid // 2
        for oy in range(-half, half + 1):
            for ox in range(-half, half + 1):
                y = cy + oy * step
                x = cx + ox * step
                value = eval_surface_at(normalized_cross_power, y, x)
                if value > best_value:
                    best_value = value
                    cy, cx = y, x
    return cy, cx, best_value


def _parabola_offset(f_minus: float, f_zero: float, f_plus: float) -> float:
    denom = f_minus - 2.0 * f_zero + f_plus
    if denom == 0.0:
        return 0.0
    offset = 0.5 * (f_minus - f_plus) / denom
    # A parabola through three points can peak outside [-1, 1] on degenerate
    # data; clamp rather than extrapolate.
    return float(np.clip(offset, -1.0, 1.0))


def refine_parabolic(surface: np.ndarray,
                     peak_index: tuple[int, int]) -> tuple[float, float]:
    """3-point parabolic refinement per axis (wrapped neighbours)."""
    h, w = surface.shape
    y, x = peak_index
    dy = _parabola_offset(surface[(y - 1) % h, x], surface[y, x], surface[(y + 1) % h, x])
    dx = _parabola_offset(surface[y, (x - 1) % w], surface[y, x], surface[y, (x + 1) % w])
    return float(y) + dy, float(x) + dx


def wrapped_to_shift(y: float, x: float, padded_shape: tuple[int, int]) -> tuple[float, float]:
    """Fractional wrapped coordinates -> signed shift (same rule as integer case)."""
    ph, pw = padded_shape
    sy = y - ph if y > ph / 2 else y
    sx = x - pw if x > pw / 2 else x
    return float(sy), float(sx)


def subpixel_estimate(surface: np.ndarray,
                      normalized_cross_power: np.ndarray,
                      peak_index: tuple[int, int],
                      rounds: tuple = ((3, 0.1), (3, 0.01))) -> dict:
    """Combine both refinements into one estimate plus an agreement metric."""
    y_dft, x_dft, peak_value = refine_dft(normalized_cross_power, peak_index, rounds)
    y_par, x_par = refine_parabolic(surface, peak_index)
    shift = wrapped_to_shift(y_dft, x_dft, surface.shape)
    par_shift = wrapped_to_shift(y_par, x_par, surface.shape)
    disagreement = float(np.hypot(shift[0] - par_shift[0], shift[1] - par_shift[1]))
    return {
        "shift": shift,
        "refined_peak_value": peak_value,
        "parabolic_shift": par_shift,
        "refine_disagreement_px": disagreement,
    }
