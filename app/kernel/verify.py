"""Overlap verification: distinguish brightness change from absent overlap.

After a shift estimate exists, the moving image is compensated by the full
sub-pixel shift (cubic spline, declared) and the overlap interior is examined
directly in the spatial domain — no FFT involved, so this is an independent
check of the frequency-domain estimate:

* Zero-mean normalised cross-correlation (NCC) is invariant to linear
  brightness transforms, so a brightness-changed pair still verifies.
* A pair with genuinely different content in the overlap yields low NCC and
  is rejected as ``NO_COMMON_CONTENT``.
* The linear gain/offset between the crops is fitted by least squares and
  reported, so "brightness changed" is a measured statement, not a guess.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage


def overlap_slices(shape: tuple[int, int], dy: float, dx: float):
    """Integer crop slices for the overlap implied by a shift.

    With the convention ``moving[y, x] == reference[y - dy, x - dx]``, the
    reference keeps coordinates ``y`` and the moving image is read at
    ``y + dy``.  Returns ``(ref_slice, mov_slice)`` as slice tuples.
    """
    h, w = shape
    iy, ix = int(round(dy)), int(round(dx))
    y0, y1 = max(0, -iy), min(h, h - iy)
    x0, x1 = max(0, -ix), min(w, w - ix)
    if y1 <= y0 or x1 <= x0:
        return None, None
    return ((slice(y0, y1), slice(x0, x1)),
            (slice(y0 + iy, y1 + iy), slice(x0 + ix, x1 + ix)))


def overlap_fraction(shape: tuple[int, int], dy: float, dx: float) -> float:
    h, w = shape
    ay = max(0.0, h - abs(dy))
    ax = max(0.0, w - abs(dx))
    return (ay * ax) / (h * w)


def normalized_cross_correlation(a: np.ndarray, b: np.ndarray) -> float:
    """Zero-mean normalised correlation; invariant to gain and offset."""
    av = a.astype(np.float64).ravel() - float(a.mean())
    bv = b.astype(np.float64).ravel() - float(b.mean())
    denom = float(np.linalg.norm(av) * np.linalg.norm(bv))
    if denom == 0.0:
        return 0.0
    return float(av @ bv / denom)


def fit_gain_offset(reference: np.ndarray, moving: np.ndarray) -> tuple[float, float]:
    """Least-squares fit of ``moving ~= gain * reference + offset``."""
    r = reference.astype(np.float64).ravel()
    m = moving.astype(np.float64).ravel()
    r_mean, m_mean = float(r.mean()), float(m.mean())
    rc = r - r_mean
    var_r = float(rc @ rc)
    if var_r == 0.0:
        return 1.0, m_mean - r_mean
    gain = float(rc @ (m - m_mean) / var_r)
    offset = m_mean - gain * r_mean
    return gain, offset


@dataclass
class Verification:
    overlap_fraction: float
    ncc: float
    gain: float
    offset: float
    offset_frac_of_range: float
    brightness_change: bool


def _aligned_crops(reference: np.ndarray, moving: np.ndarray,
                   dy: float, dx: float) -> tuple[np.ndarray, np.ndarray] | None:
    """Overlap crops with the moving image sub-pixel-compensated.

    The moving image is shifted by ``(-dy, -dx)`` (cubic spline) so both crops
    cover the same content; a 2 px interior margin excludes resampling border
    artifacts.  Falls back to integer crops when the image is too small.
    """
    h, w = reference.shape
    my = int(np.ceil(abs(dy))) + 2
    mx = int(np.ceil(abs(dx))) + 2
    if h - 2 * my >= 8 and w - 2 * mx >= 8:
        compensated = ndimage.shift(moving, (-dy, -dx), order=3, mode="nearest")
        return (reference[my:h - my, mx:w - mx],
                compensated[my:h - my, mx:w - mx])
    ref_sl, mov_sl = overlap_slices(reference.shape, dy, dx)
    if ref_sl is None:
        return None
    return reference[ref_sl], moving[mov_sl]


def verify_alignment(reference: np.ndarray, moving: np.ndarray,
                     dy: float, dx: float,
                     ncc_threshold: float,
                     brightness_gain_tol: float,
                     brightness_offset_tol_frac: float) -> Verification | None:
    """Verify a shift in the spatial domain; ``None`` if there is no overlap."""
    crops = _aligned_crops(reference, moving, dy, dx)
    if crops is None:
        return None
    ref_crop, mov_crop = crops
    ncc = normalized_cross_correlation(ref_crop, mov_crop)
    gain, offset = fit_gain_offset(ref_crop, mov_crop)
    value_range = max(float(reference.max()) - float(reference.min()), 1e-12)
    offset_frac = abs(offset) / value_range
    brightness_change = (abs(gain - 1.0) > brightness_gain_tol
                         or offset_frac > brightness_offset_tol_frac)
    return Verification(
        overlap_fraction=overlap_fraction(reference.shape, dy, dx),
        ncc=ncc,
        gain=gain,
        offset=offset,
        offset_frac_of_range=offset_frac,
        brightness_change=brightness_change,
    )
