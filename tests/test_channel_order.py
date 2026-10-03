"""Channel-order tests: RGB must not silently become BGR, CMYK stays CMYK."""
from __future__ import annotations

import numpy as np

from colorconvert.contract import ColorMode, ImageData
from colorconvert.kernel import RenderingIntent

REL = RenderingIntent.RELATIVE_COLORIMETRIC


def _one_pixel(kernel, registry, src_id, dst_id, pixel, mode, allow_cmyk=False):
    src = registry.get(src_id, role="source")
    dst = registry.get(dst_id, role="target")
    image = ImageData(
        color=np.array([[pixel]], dtype=np.uint8), mode=mode
    )
    result, _ = kernel.convert(image, src, dst, REL, False)
    return result.color[0, 0].astype(int)


def test_identity_preserves_channel_order(kernel, registry):
    out = _one_pixel(kernel, registry, "srgb", "srgb", (10, 20, 30), ColorMode.RGB)
    assert tuple(out) == (10, 20, 30), "identity conversion reordered channels"


def test_swapped_red_green_profile_swaps_exactly(kernel, registry):
    # The SwappedRedAndGreen profile exists precisely to detect channel
    # order bugs: applying it must exchange R and G.
    out = _one_pixel(
        kernel, registry, "srgb", "swapped-rg", (10, 200, 30), ColorMode.RGB
    )
    assert abs(out[0] - 200) <= 1 and abs(out[1] - 10) <= 1
    assert abs(out[2] - 30) <= 1


def test_cmyk_channel_order(kernel, registry):
    # Pure cyan ink (C=255, M=Y=K=0) must come back as cyan-ish RGB:
    # low red, high green and blue.  Any C/M/Y permutation breaks this.
    out = _one_pixel(
        kernel, registry, "fogra39-cmyk", "srgb",
        (255, 0, 0, 0), ColorMode.CMYK,
    )
    r, g, b = (int(v) for v in out)
    assert r < g and r < b, f"cyan ink mapped to {(r, g, b)}"
    assert g > 150 and b > 150, f"cyan ink mapped to {(r, g, b)}"


def test_lab_channel_order(kernel, registry):
    # sRGB red: L*~54, a*~+81, b*~+70 (signed 8-bit storage, see contract).
    out = _one_pixel(kernel, registry, "srgb", "lab-d50", (255, 0, 0), ColorMode.RGB)
    L, a, b = (int(v) for v in out)
    assert 130 < L < 145
    assert 70 < a < 95, f"a* byte {a} out of expected range for red"
    assert 60 < b < 85, f"b* byte {b} out of expected range for red"
