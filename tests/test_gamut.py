"""Out-of-gamut heuristic tests."""

from __future__ import annotations

import numpy as np

from iccconv.contract import ColorSpace, ImageDocument, RenderingIntent
from iccconv.kernel.engine import convert_document
from iccconv.kernel.gamut import estimate_gamut


def test_saturated_primaries_flagged_for_cmyk(registry):
    src = registry.load("sRGB.icc")
    dst = registry.load("SWOP_TR003_coated_3.icc")
    image = np.zeros((2, 4, 3), dtype=np.uint8)
    image[0, 0] = [255, 0, 0]
    image[0, 1] = [0, 255, 0]
    image[0, 2] = [0, 0, 255]
    image[0, 3] = [128, 128, 128]
    image[1, :] = [[200, 180, 160], [40, 40, 40], [255, 255, 255], [90, 150, 220]]

    report = estimate_gamut(
        image, src[0], dst[0], ColorSpace.RGB, ColorSpace.CMYK,
        RenderingIntent.RELATIVE_COLORIMETRIC, False, tolerance=3,
    )
    assert report.method == "roundtrip-heuristic"
    assert report.certainty == "heuristic"
    assert report.flagged_pixels >= 3  # the three pure primaries at minimum
    assert report.total_pixels == 8


def test_neutral_gray_not_flagged(registry):
    src = registry.load("sRGB.icc")
    dst = registry.load("SWOP_TR003_coated_3.icc")
    image = np.full((4, 4, 3), 128, dtype=np.uint8)
    report = estimate_gamut(
        image, src[0], dst[0], ColorSpace.RGB, ColorSpace.CMYK,
        RenderingIntent.RELATIVE_COLORIMETRIC, False, tolerance=3,
    )
    assert report.flagged_pixels == 0


def test_engine_reports_gamut_when_requested(registry):
    doc = ImageDocument(np.array([[[255, 0, 0]]], dtype=np.uint8), ColorSpace.RGB)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("SWOP_TR003_coated_3.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
        check_gamut=True,
    )
    assert result.gamut is not None
    assert result.gamut.flagged_pixels == 1
