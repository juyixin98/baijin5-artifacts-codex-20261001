"""Contract tests: ImageDocument validation rules."""

from __future__ import annotations

import numpy as np
import pytest

from iccconv.contract import AlphaMode, ColorSpace, ImageDocument
from iccconv.errors import ContractViolationError, UnsupportedAlphaError


def test_rgb_document_ok():
    doc = ImageDocument(np.zeros((4, 4, 3), np.uint8), ColorSpace.RGB)
    assert not doc.has_alpha
    assert doc.base_channels == 3


def test_rgba_straight_ok():
    doc = ImageDocument(np.zeros((4, 4, 4), np.uint8), ColorSpace.RGB, AlphaMode.STRAIGHT)
    assert doc.has_alpha


def test_gray_with_alpha_ok():
    doc = ImageDocument(np.zeros((4, 4, 2), np.uint8), ColorSpace.GRAY, AlphaMode.STRAIGHT)
    assert doc.has_alpha


def test_rejects_non_uint8():
    with pytest.raises(ContractViolationError):
        ImageDocument(np.zeros((4, 4, 3), np.float32), ColorSpace.RGB)


def test_rejects_2d_pixels():
    with pytest.raises(ContractViolationError):
        ImageDocument(np.zeros((4, 4), np.uint8), ColorSpace.GRAY)


def test_rejects_channel_mismatch():
    with pytest.raises(ContractViolationError) as excinfo:
        ImageDocument(np.zeros((4, 4, 4), np.uint8), ColorSpace.RGB, AlphaMode.NONE)
    assert excinfo.value.detail["expected_channels"] == 3


def test_rejects_cmyk_with_alpha():
    with pytest.raises(UnsupportedAlphaError):
        ImageDocument(np.zeros((4, 4, 5), np.uint8), ColorSpace.CMYK, AlphaMode.STRAIGHT)


def test_rejects_empty_image():
    with pytest.raises(ContractViolationError):
        ImageDocument(np.zeros((0, 4, 3), np.uint8), ColorSpace.RGB)


def test_enum_strings_normalized():
    doc = ImageDocument(np.zeros((2, 2, 3), np.uint8), "RGB", "none")
    assert doc.color_space is ColorSpace.RGB
    assert doc.alpha_mode is AlphaMode.NONE
