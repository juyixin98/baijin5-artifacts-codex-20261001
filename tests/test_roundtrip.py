"""Round-trip error characterization.

These tests document — with numbers — that 8-bit cross-profile conversion is
NOT lossless, and that the loss stays within expected bounds per path.  The
kernel report must agree (``lossy`` flag, notes).
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter

from colorconvert.contract import ColorMode, ImageData
from colorconvert.kernel import RenderingIntent

REL = RenderingIntent.RELATIVE_COLORIMETRIC


def _gradient(h=16, w=64):
    """Smooth synthetic photo-like image (scipy-generated fixture)."""
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 256, (h, w, 3)).astype(np.float64)
    smooth = gaussian_filter(noise, sigma=(3.0, 3.0, 0.0))
    lo, hi = smooth.min(), smooth.max()
    return ((smooth - lo) / (hi - lo) * 255.0).round().astype(np.uint8)


def _roundtrip(kernel, registry, there_id, back_id, image, bpc=False):
    src = registry.get("srgb", role="source")
    there = registry.get(there_id, role="target")
    back = registry.get(back_id, role="source")
    dst = registry.get("srgb", role="target")
    mid, rep1 = kernel.convert(image, src, there, REL, bpc)
    out, rep2 = kernel.convert(mid, back, dst, REL, bpc)
    return out, rep1, rep2


def test_srgb_adobe_roundtrip_within_quantization(kernel, registry):
    image = ImageData(color=_gradient(), mode=ColorMode.RGB)
    out, rep1, _ = _roundtrip(kernel, registry, "adobe-rgb", "adobe-rgb", image)
    err = np.abs(out.color.astype(int) - image.color.astype(int))
    # sRGB gamut fits inside Adobe RGB: only 8-bit quantization hurts us.
    # Measured on this fixture: max 3 LSB (two 8-bit transform roundings).
    assert err.max() <= 3, f"round-trip error {err.max()} exceeds quantization bound"
    assert rep1.lossy is True  # cross-profile: never claimed lossless


def test_identity_is_not_marked_lossy(kernel, registry):
    src = registry.get("srgb", role="source")
    dst = registry.get("srgb", role="target")
    image = ImageData(color=_gradient(), mode=ColorMode.RGB)
    out, report = kernel.convert(image, src, dst, REL, False)
    assert report.lossy is False
    assert np.array_equal(out.color, image.color)


def test_cmyk_roundtrip_is_visibly_lossy_for_saturated_colors(kernel, registry):
    saturated = np.zeros((2, 4, 3), np.uint8)
    saturated[0] = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    saturated[1] = [(128, 128, 128), (64, 64, 64), (192, 192, 192), (255, 255, 255)]
    image = ImageData(color=saturated, mode=ColorMode.RGB)
    out, rep1, _ = _roundtrip(
        kernel, registry, "fogra39-cmyk", "fogra39-cmyk", image, bpc=True
    )
    err = np.abs(out.color.astype(int) - image.color.astype(int))
    sat_err = err[0].max()
    gray_err = err[1].max()
    # Saturated sRGB colors exceed the FOGRA39 gamut: error must be visible.
    assert sat_err > 10, f"expected visible gamut loss, got max err {sat_err}"
    # Neutral grays survive much better (they are in gamut).
    assert gray_err <= 8, f"gray ramp round-trip error {gray_err} too large"
    assert rep1.lossy is True
    assert any("CMYK" in n for n in rep1.notes)


def test_black_point_compensation_is_recorded(kernel, registry):
    src = registry.get("srgb", role="source")
    dst = registry.get("fogra39-cmyk", role="target")
    image = ImageData(color=_gradient(2, 8), mode=ColorMode.RGB)
    _, report = kernel.convert(image, src, dst, REL, True)
    assert report.black_point_compensation is True
    _, report2 = kernel.convert(image, src, dst, REL, False)
    assert report2.black_point_compensation is False
