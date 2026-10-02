"""FFT phase-correlation core.

Declared conventions (these are part of the behavioural contract):

* Shift ``(dy, dx)`` means ``moving[y, x] == reference[y - dy, x - dx]`` on the
  overlap region.
* A separable Hann window is applied to both images by default to suppress the
  discontinuity between opposite borders (otherwise the DFT's implicit
  circularity would invent edges that dominate the spectrum).
* Images are zero-padded to ``pad_factor * shape`` *after* windowing.  This is
  declared padding: it extends the unambiguous shift range from ``(-n/2, n/2]``
  to ``(-n, n]`` and is reflected in every reported quantity.
* Bins of the cross-power spectrum whose magnitude is below
  ``eps_rel * max|R|`` carry no phase information.  They are set to exactly
  zero (never divided by), and their fraction is reported as
  ``zero_bin_fraction`` so a degenerate spectrum is visible to callers.
* Remaining DFT circularity is *not* hidden: shifts alias modulo the padded
  size, and shifts whose geometric overlap falls below ``min_overlap`` are
  flagged by the pipeline instead of being silently trusted.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KERNEL_VERSION = "1.0.0"


def hann_window(shape: tuple[int, int]) -> np.ndarray:
    """Separable 2-D Hann window (zero at the borders)."""
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1]))


@dataclass
class CrossPower:
    normalized: np.ndarray      # R / |R| with dead bins explicitly zeroed
    zero_bin_fraction: float    # fraction of bins that were zeroed
    n_bins: int


def cross_power_spectrum(reference: np.ndarray, moving: np.ndarray,
                         eps_rel: float = 1e-12) -> CrossPower:
    """Normalised cross-power spectrum with explicit dead-bin handling.

    ``R = F_mov * conj(F_ref)`` so that the correlation peak lands directly at
    ``(dy, dx)`` under the convention documented in the module docstring.
    """
    F = np.fft.fft2(reference)
    G = np.fft.fft2(moving)
    R = G * np.conj(F)
    mag = np.abs(R)
    peak_mag = mag.max()
    normalised = np.zeros_like(R)
    if peak_mag > 0.0:
        alive = mag > eps_rel * peak_mag
        normalised[alive] = R[alive] / mag[alive]
        zero_fraction = 1.0 - float(alive.mean())
    else:
        # Both spectra are identically zero (all-zero images): every bin dead.
        zero_fraction = 1.0
    return CrossPower(normalised, zero_fraction, R.size)


@dataclass
class Surface:
    values: np.ndarray          # real correlation surface (padded size)
    cross_power: CrossPower
    padded_shape: tuple[int, int]
    original_shape: tuple[int, int]


def correlation_surface(reference: np.ndarray, moving: np.ndarray,
                        apply_window: bool = True,
                        pad_factor: int = 2,
                        eps_rel: float = 1e-12,
                        remove_mean: bool = True) -> Surface:
    """Mean-remove (declared), window, pad (declared), compute surface."""
    if reference.shape != moving.shape:
        raise ValueError(
            f"shape mismatch: reference{reference.shape} vs moving{moving.shape}")
    ref = reference.astype(np.float64, copy=False)
    mov = moving.astype(np.float64, copy=False)
    if remove_mean:
        ref = ref - ref.mean()
        mov = mov - mov.mean()
    if apply_window:
        w = hann_window(ref.shape)
        ref = ref * w
        mov = mov * w
    if pad_factor > 1:
        padded = (ref.shape[0] * pad_factor, ref.shape[1] * pad_factor)
        ref = np.pad(ref, ((0, padded[0] - ref.shape[0]),
                           (0, padded[1] - ref.shape[1])))
        mov = np.pad(mov, ((0, padded[0] - mov.shape[0]),
                           (0, padded[1] - mov.shape[1])))
    cp = cross_power_spectrum(ref, mov, eps_rel=eps_rel)
    surface = np.fft.ifft2(cp.normalized).real
    return Surface(surface, cp, ref.shape, reference.shape)


def index_to_shift(index: tuple[int, int], padded_shape: tuple[int, int]) -> tuple[float, float]:
    """Map a surface index to a signed shift (DFT aliasing made explicit)."""
    dy, dx = index
    ph, pw = padded_shape
    sy = dy - ph if dy > ph // 2 else dy
    sx = dx - pw if dx > pw // 2 else dx
    return float(sy), float(sx)


def integer_peak(surface: np.ndarray) -> tuple[tuple[int, int], tuple[float, float]]:
    """Locate the global maximum; return its index and signed integer shift."""
    idx = np.unravel_index(int(np.argmax(surface)), surface.shape)
    return (int(idx[0]), int(idx[1])), index_to_shift(idx, surface.shape)


def find_candidate_peaks(surface: np.ndarray, ratio: float,
                         max_candidates: int, mask_radius: int
                         ) -> list[dict]:
    """All peaks within ``ratio`` of the global max (ambiguity detection).

    Returns a list of ``{"index", "shift", "height"}`` sorted by descending
    height.  Periodic textures legitimately produce several equal-height peaks;
    callers must treat more than one candidate as an ambiguous estimate.
    """
    work = surface.copy()
    global_peak = float(work.max())
    candidates: list[dict] = []
    h, w = work.shape
    for _ in range(max_candidates):
        idx = np.unravel_index(int(np.argmax(work)), work.shape)
        height = float(work[idx])
        if height < ratio * global_peak and candidates:
            break
        candidates.append({
            "index": (int(idx[0]), int(idx[1])),
            "shift": index_to_shift((int(idx[0]), int(idx[1])), work.shape),
            "height": height,
        })
        # Mask a wrapped neighbourhood so the same peak is not re-found.
        for oy in range(-mask_radius, mask_radius + 1):
            for ox in range(-mask_radius, mask_radius + 1):
                work[(idx[0] + oy) % h, (idx[1] + ox) % w] = -np.inf
        if height < ratio * global_peak:
            break
    return candidates


def select_primary_peak(candidates: list[dict], rel_tol: float = 1e-9) -> dict:
    """Choose the primary peak: highest, ties broken by minimum shift norm.

    Declared tie-breaking rule: among equal-height candidates (periodic
    textures alias their peak across the whole search range, including the
    wrap border) the shift closest to zero is the primary estimate.  The
    remaining candidates are still reported as ambiguity.
    """
    best_height = max(c["height"] for c in candidates)
    tied = [c for c in candidates if c["height"] >= best_height * (1.0 - rel_tol)]
    return min(tied, key=lambda c: c["shift"][0] ** 2 + c["shift"][1] ** 2)


def peak_statistics(surface: np.ndarray, peak_index: tuple[int, int],
                    exclude_radius: int) -> dict:
    """Peak height, peak-to-sidelobe ratio and surface contrast."""
    h, w = surface.shape
    mask = np.ones_like(surface, dtype=bool)
    py, px = peak_index
    for oy in range(-exclude_radius, exclude_radius + 1):
        for ox in range(-exclude_radius, exclude_radius + 1):
            mask[(py + oy) % h, (px + ox) % w] = False
    sidelobe = surface[mask]
    peak = float(surface[peak_index])
    side_std = float(sidelobe.std())
    psr = (peak - float(sidelobe.mean())) / side_std if side_std > 0 else float("inf")
    # Contrast is measured against the mean *absolute* value: after mean
    # removal the surface mean itself is ~0, so mean-based ratios are useless.
    contrast = float(surface.std() / (np.abs(surface).mean() + 1e-300))
    return {
        "peak_height": peak,
        "peak_to_sidelobe": psr,
        "surface_contrast": contrast,
        "surface_mean": float(surface.mean()),
        "surface_std": float(surface.std()),
    }
