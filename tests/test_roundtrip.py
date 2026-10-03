"""Round-trip error statements: what is preserved and what is not."""

from __future__ import annotations

import numpy as np

from iccconv.contract import ColorSpace, ImageDocument, RenderingIntent
from iccconv.kernel.engine import convert_document

# Smooth, in-gamut-for-both colors (no saturated sRGB primaries).
IN_GAMUT = np.array(
    [[128, 128, 128], [200, 180, 160], [90, 150, 220], [60, 140, 70],
     [180, 60, 60], [40, 40, 40], [220, 220, 220], [100, 100, 160]],
    dtype=np.uint8,
).reshape(2, 4, 3)

SATURATED = np.array(
    [[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 0]], dtype=np.uint8
).reshape(2, 2, 3)


def _roundtrip(image, registry, mid_profile):
    src = registry.load("sRGB.icc")
    mid = registry.load(mid_profile)
    there = convert_document(
        ImageDocument(image, ColorSpace.RGB), source=src, target=mid,
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    ).document
    back = convert_document(
        there, source=mid, target=src, intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    ).document
    return back.pixels


def test_rgb_roundtrip_error_is_small(registry):
    back = _roundtrip(IN_GAMUT, registry, "AdobeRGB1998.icc")
    err = np.abs(back.astype(int) - IN_GAMUT.astype(int))
    # Statement: sRGB -> AdobeRGB -> sRGB round-trip error <= 2 LSB for
    # colors inside both gamuts (quantization only, no gamut mapping).
    assert int(err.max()) <= 2, f"round-trip max error {int(err.max())}"


def test_cmyk_roundtrip_loses_saturated_colors(registry):
    back = _roundtrip(SATURATED, registry, "SWOP_TR003_coated_3.icc")
    err = np.abs(back.astype(int) - SATURATED.astype(int)).max(axis=-1)
    # Statement: saturated sRGB primaries do NOT survive a CMYK round trip;
    # the service never claims lossless cross-gamut conversion.
    assert int(err.max()) > 10
    result = convert_document(
        ImageDocument(SATURATED, ColorSpace.RGB),
        source=registry.load("sRGB.icc"),
        target=registry.load("SWOP_TR003_coated_3.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    assert result.metadata.lossless is False
