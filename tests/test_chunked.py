"""Chunked job tests: tiling is bit-identical to whole-image conversion."""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from iccconv.contract import ColorSpace, ImageDocument, RenderingIntent
from iccconv.errors import ContractViolationError
from iccconv.jobs.chunked import convert_chunked, tile_slices
from iccconv.kernel.engine import convert_document


def _doc(h=20, w=20):
    rng = np.random.default_rng(5)
    return ImageDocument(rng.integers(0, 256, size=(h, w, 3), dtype=np.uint8), ColorSpace.RGB)


def _convert_fn(registry):
    src = registry.load("sRGB.icc")
    dst = registry.load("AdobeRGB1998.icc")

    def fn(doc):
        return convert_document(
            doc, source=src, target=dst, intent=RenderingIntent.RELATIVE_COLORIMETRIC
        )

    return fn


def test_tile_slices_cover_image():
    slices = list(tile_slices(20, 20, 8))
    assert len(slices) == 9
    assert slices[0] == (slice(0, 8), slice(0, 8))
    assert slices[-1] == (slice(16, 20), slice(16, 20))


def test_chunked_equals_whole_image(registry):
    doc = _doc()
    fn = _convert_fn(registry)
    whole = fn(doc).document.pixels
    chunked, job = convert_chunked(doc, tile_size=8, convert_fn=fn)
    assert np.array_equal(chunked.pixels, whole)
    assert job.status == "completed"
    assert len(job.tiles) == 9
    assert job.result_sha256 == hashlib.sha256(chunked.pixels.tobytes()).hexdigest()
    for tile in job.tiles:
        assert len(tile.sha256) == 64


def test_tile_larger_than_image_is_single_tile(registry):
    doc = _doc(6, 6)
    chunked, job = convert_chunked(doc, tile_size=64, convert_fn=_convert_fn(registry))
    assert len(job.tiles) == 1
    assert np.array_equal(chunked.pixels, _convert_fn(registry)(doc).document.pixels)


def test_invalid_tile_size_rejected(registry):
    with pytest.raises(ContractViolationError):
        convert_chunked(_doc(), tile_size=0, convert_fn=_convert_fn(registry))
