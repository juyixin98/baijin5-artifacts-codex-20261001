"""FFT phase-correlation surfaces with explicit degenerate-bin handling.

Declared behaviour (contract item 1):

* Images are mean-subtracted, std-normalised and (per config) Hann-windowed
  so the DFT's circular assumption does not turn edges into leakage.
* Spectra are zero-padded by ``pad_factor`` so the returned surface is the
  *linear* correlation over the declared valid range
  ``|dy| <= H//2, |dx| <= W//2``. Shifts outside that range are out of
  contract rather than silently wrapped.
* Cross-power bins with magnitude ``<= eps_ratio * max`` are set to zero
  (never divided) and counted; the caller sees ``degenerate_fraction``.

Two magnitude floors are used for two different jobs (see pipeline):

* estimation floor (~1e-12): keeps every numerically meaningful bin, giving
  the sharpest peak and the best subpixel accuracy;
* ambiguity floor (~1e-3): keeps only true spectral lines, so a periodic
  texture's lattice replicas survive whitening and remain detectable
  instead of being flattened into a single delta peak.

Sign convention: ``img2[y, x] ~= img1[y - dy, x - dx]`` — the returned
shift ``(dy, dx)`` is the translation of image B relative to image A.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.fft import fft2, fftshift, ifft2, next_fast_len
from scipy.ndimage import maximum_filter

from app.config import KernelConfig
from app.kernel.windows import make_window


@dataclass
class Peak:
    dy: int
    dx: int
    value: float


@dataclass
class CrossPower:
    """Windowed, normalised cross-power spectrum of an image pair."""

    cross: np.ndarray
    mag: np.ndarray
    image_shape: tuple[int, int]
    padded_shape: tuple[int, int]


@dataclass
class PhaseCorrSurface:
    surface: np.ndarray  # cropped valid region; zero lag at (max_shift)
    max_shift: tuple[int, int]  # (my, mx): valid range is +/- this
    integer_shift: tuple[int, int]
    peak_value: float
    degenerate_bins: int
    total_bins: int
    eps: float
    peaks: list[Peak]  # top-K local maxima, descending value

    @property
    def degenerate_fraction(self) -> float:
        return self.degenerate_bins / self.total_bins


def _normalize(img: np.ndarray) -> np.ndarray:
    a = img - img.mean()
    std = a.std()
    # Constant image: std == 0 -> all-zero spectrum, caught downstream as
    # a fully degenerate cross-power spectrum. Never divide by zero.
    return a / std if std > 0 else a


def _top_peaks(
    surface: np.ndarray, my: int, mx: int, count: int
) -> list[Peak]:
    """Top-``count`` local maxima with 2-px non-maximum suppression."""
    local_max = surface == maximum_filter(surface, size=3, mode="nearest")
    iy, ix = np.nonzero(local_max)
    order = np.argsort(surface[iy, ix])[::-1]
    peaks: list[Peak] = []
    for idx in order:
        y, x = int(iy[idx]), int(ix[idx])
        if any(abs(y - (p.dy + my)) <= 2 and abs(x - (p.dx + mx)) <= 2 for p in peaks):
            continue
        peaks.append(Peak(dy=y - my, dx=x - mx, value=float(surface[y, x])))
        if len(peaks) >= count:
            break
    return peaks


def cross_power(
    img1: np.ndarray, img2: np.ndarray, cfg: KernelConfig
) -> CrossPower:
    """Window + normalise + zero-pad + FFT both images, return F2*conj(F1)."""
    if img1.shape != img2.shape:
        raise ValueError(
            f"shape mismatch: {img1.shape} vs {img2.shape} "
            "(contract requires same-shape grayscale pair)"
        )
    if img1.ndim != 2:
        raise ValueError(f"expected 2-D grayscale, got ndim={img1.ndim}")

    h, w = img1.shape
    win = make_window((h, w), cfg.window)
    a = _normalize(img1) * win
    b = _normalize(img2) * win

    ph, pw = next_fast_len(h * cfg.pad_factor), next_fast_len(w * cfg.pad_factor)
    f1 = fft2(a, s=(ph, pw))
    f2 = fft2(b, s=(ph, pw))
    # Peak lands at +d for img2 = img1 shifted by d (see module docstring).
    cross = f2 * np.conj(f1)
    return CrossPower(
        cross=cross,
        mag=np.abs(cross),
        image_shape=(h, w),
        padded_shape=(ph, pw),
    )


def normalized_surface(
    cp: CrossPower, eps_ratio: float, peak_count: int
) -> PhaseCorrSurface:
    """Whitened (phase-only) correlation surface at a given magnitude floor."""
    h, w = cp.image_shape
    ph, pw = cp.padded_shape
    mmax = float(cp.mag.max())
    eps = eps_ratio * mmax
    norm = np.zeros_like(cp.cross)
    if mmax > 0.0:
        keep = cp.mag > eps
        norm[keep] = cp.cross[keep] / cp.mag[keep]
    else:
        keep = np.zeros(cp.mag.shape, dtype=bool)
    degenerate = int(cp.mag.size - keep.sum())

    surface_full = np.real(ifft2(norm))
    shifted = fftshift(surface_full)  # zero lag moves to (ph//2, pw//2)
    my, mx = h // 2, w // 2
    cy, cx = ph // 2, pw // 2
    cropped = shifted[cy - my : cy + my + 1, cx - mx : cx + mx + 1]

    flat = int(np.argmax(cropped))
    iy, ix = np.unravel_index(flat, cropped.shape)
    integer_shift = (int(iy - my), int(ix - mx))

    return PhaseCorrSurface(
        surface=cropped,
        max_shift=(my, mx),
        integer_shift=integer_shift,
        peak_value=float(cropped[iy, ix]),
        degenerate_bins=degenerate,
        total_bins=int(cp.mag.size),
        eps=eps,
        peaks=_top_peaks(cropped, my, mx, peak_count),
    )


def cross_power_surface(
    img1: np.ndarray, img2: np.ndarray, cfg: KernelConfig
) -> PhaseCorrSurface:
    """Convenience: estimation surface straight from an image pair."""
    return normalized_surface(cross_power(img1, img2, cfg), cfg.eps_ratio, cfg.peak_count)
