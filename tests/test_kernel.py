"""Kernel tests against engine golden values (littleCMS, generated directly)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import ImageCms

from iccconv.contract import AlphaMode, ColorSpace, ImageDocument, RenderingIntent
from iccconv.kernel.engine import convert_document

_SPACE_BY_CHANNELS = {1: ColorSpace.GRAY, 3: ColorSpace.RGB, 4: ColorSpace.CMYK}


def _case_doc(case: dict) -> ImageDocument:
    arr = np.array(case["input"], dtype=np.uint8)
    channels = arr.shape[1]
    pixels = arr.reshape(1, len(arr), channels)
    return ImageDocument(pixels, _SPACE_BY_CHANNELS[channels], AlphaMode.NONE)


def _engine_matches(golden: dict) -> bool:
    return golden["engine"]["lcms"] == ImageCms.versions()[1]


def test_golden_cases_match_kernel(golden, registry):
    if not _engine_matches(golden):
        pytest.skip(
            f"golden values generated with lcms {golden['engine']['lcms']}, "
            f"current engine is {ImageCms.versions()[1]}; re-run scripts/generate_golden.py"
        )
    mismatches = []
    for case in golden["cases"]:
        doc = _case_doc(case)
        result = convert_document(
            doc,
            source=registry.load(case["src_profile"]),
            target=registry.load(case["dst_profile"]),
            intent=RenderingIntent[case["intent"].upper()],
            black_point_compensation=case["black_point_compensation"],
        )
        got = result.document.pixels.reshape(len(case["input"]), -1).tolist()
        if got != case["expected"]:
            mismatches.append((case["id"], case["expected"], got))
    assert not mismatches, f"golden mismatches: {mismatches[:3]}"


def test_identity_conversion_is_exact(golden, registry):
    case = next(c for c in golden["cases"] if c["id"] == "srgb_to_srgb_identity")
    doc = _case_doc(case)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("sRGB.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    assert np.array_equal(result.document.pixels.reshape(-1, 3), np.array(case["input"]))


def test_channel_order_is_rgb_not_bgr(golden, registry):
    """A profile with swapped R/G primaries proves channel order end-to-end."""
    case = next(c for c in golden["cases"] if c["id"] == "srgb_to_swapped_rg_relcol")
    names = case["patch_names"]
    red_out = case["expected"][names.index("red")]
    green_out = case["expected"][names.index("green")]
    assert red_out == [0, 255, 0] and green_out == [255, 0, 0]

    doc = _case_doc(case)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("SwappedRedAndGreen.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    got = result.document.pixels.reshape(-1, 3).tolist()
    assert got[names.index("red")] == [0, 255, 0]
    assert got[names.index("green")] == [255, 0, 0]


def test_metadata_records_intent_and_bpc(registry):
    doc = ImageDocument(np.zeros((2, 2, 3), np.uint8), ColorSpace.RGB)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("SWOP_TR003_coated_3.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
        black_point_compensation=True,
    )
    assert result.metadata.rendering_intent == "RELATIVE_COLORIMETRIC"
    assert result.metadata.black_point_compensation is True
    assert result.metadata.lossless is False
    assert "never lossless" in result.metadata.gamut_note
    assert result.document.color_space is ColorSpace.CMYK
