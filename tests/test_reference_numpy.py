"""Independent NumPy/SciPy reference vs. the littleCMS-backed kernel."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from iccconv.contract import ColorSpace, ImageDocument, RenderingIntent
from iccconv.kernel.engine import convert_document

sys.path.insert(0, str(Path(__file__).resolve().parent / "reference"))
from numpy_reference import srgb_to_adobergb_reference  # noqa: E402


def test_kernel_matches_independent_reference(registry):
    """sRGB -> AdobeRGB (relative colorimetric) within 1 LSB of first-principles math."""
    rng = np.random.default_rng(42)
    grid = rng.integers(0, 256, size=(128, 3), dtype=np.uint8)
    patches = np.array(
        [[0, 0, 0], [255, 255, 255], [255, 0, 0], [0, 255, 0], [0, 0, 255],
         [128, 128, 128], [222, 184, 160], [90, 150, 220]], dtype=np.uint8,
    )
    image = np.concatenate([grid, patches], axis=0).reshape(8, 17, 3)

    doc = ImageDocument(image, ColorSpace.RGB)
    result = convert_document(
        doc,
        source=registry.load("sRGB.icc"),
        target=registry.load("AdobeRGB1998.icc"),
        intent=RenderingIntent.RELATIVE_COLORIMETRIC,
    )
    expected = srgb_to_adobergb_reference(image)
    diff = np.abs(result.document.pixels.astype(int) - expected.astype(int))
    # littleCMS uses its own Bradford matrix and a fixed-point pipeline;
    # agreement within 1 LSB per channel is the acceptance bound.
    assert int(diff.max()) <= 1, f"max diff {int(diff.max())} exceeds 1 LSB"
