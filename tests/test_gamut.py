"""Out-of-gamut behavior: loss is demonstrated, never hidden."""
from __future__ import annotations

import numpy as np

from colorconvert.contract import ColorMode, ImageData
from colorconvert.kernel import RenderingIntent

REL = RenderingIntent.RELATIVE_COLORIMETRIC


def test_prophoto_green_clipped_into_srgb(kernel, registry):
    prophoto = registry.get("prophoto-rgb", role="source")
    srgb = registry.get("srgb", role="target")
    # ProPhoto pure green is far outside the sRGB gamut.
    image = ImageData(
        color=np.array([[(0, 255, 0)]], dtype=np.uint8), mode=ColorMode.RGB
    )
    narrowed, rep1 = kernel.convert(image, prophoto, srgb, REL, False)
    back, rep2 = kernel.convert(
        ImageData(color=narrowed.color, mode=ColorMode.RGB),
        registry.get("srgb", role="source"),
        registry.get("prophoto-rgb", role="target"),
        REL,
        False,
    )
    err = np.abs(back.color.astype(int) - image.color.astype(int)).max()
    assert err > 10, (
        "ProPhoto green -> sRGB -> ProPhoto should lose gamut visibly; "
        f"got max err {err}"
    )
    assert rep1.lossy and rep2.lossy


def test_in_gamut_roundtrip_via_prophoto_is_small(kernel, registry):
    # sRGB green fits inside ProPhoto: sRGB->ProPhoto->sRGB stays tight.
    srgb_src = registry.get("srgb", role="source")
    prophoto = registry.get("prophoto-rgb", role="target")
    prophoto_src = registry.get("prophoto-rgb", role="source")
    srgb_dst = registry.get("srgb", role="target")
    image = ImageData(
        color=np.array([[(0, 255, 0), (12, 200, 190)]], dtype=np.uint8),
        mode=ColorMode.RGB,
    )
    wide, _ = kernel.convert(image, srgb_src, prophoto, REL, False)
    back, _ = kernel.convert(wide, prophoto_src, srgb_dst, REL, False)
    err = np.abs(back.color.astype(int) - image.color.astype(int)).max()
    # In-gamut colors survive; the bound is 8-bit quantization through a
    # very wide gamut (measured max on this fixture: 4 LSB).
    assert err <= 5, f"in-gamut round trip drifted {err}"


def test_service_report_marks_cmyk_target_lossy(service, log):
    from colorconvert.service import ConversionRequest

    image = ImageData(
        color=np.full((2, 2, 3), (0, 255, 0), np.uint8), mode=ColorMode.RGB
    )
    req = ConversionRequest(
        image=image,
        source_profile="srgb",
        target_profile="fogra39-cmyk",
        allow_cmyk=True,
    )
    outcome = service.convert(req, log)
    assert outcome.job.status.value == "completed"
    report = outcome.job.report
    assert report["lossy"] is True
    assert any("CMYK" in n for n in report["notes"])
