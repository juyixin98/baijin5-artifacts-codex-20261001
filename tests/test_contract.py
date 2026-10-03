"""Contract tests: array layout, dtypes, alpha semantics declarations."""
from __future__ import annotations

import numpy as np
import pytest

from colorconvert.contract import CHANNEL_ORDER, ColorMode, ImageData
from colorconvert.errors import ContractError, FailureCategory


def test_valid_rgb_image_passes():
    img = ImageData(color=np.zeros((4, 4, 3), np.uint8), mode=ColorMode.RGB)
    img.validate()  # no raise


def test_valid_cmyk_with_alpha_passes():
    img = ImageData(
        color=np.zeros((4, 4, 4), np.uint8),
        mode=ColorMode.CMYK,
        alpha=np.full((4, 4), 255, np.uint8),
    )
    img.validate()


def test_wrong_dtype_rejected():
    img = ImageData(color=np.zeros((4, 4, 3), np.float32), mode=ColorMode.RGB)
    with pytest.raises(ContractError) as ei:
        img.validate()
    assert ei.value.category is FailureCategory.CONTRACT_VIOLATION
    assert "uint8" in ei.value.message


def test_wrong_channel_count_rejected():
    img = ImageData(color=np.zeros((4, 4, 4), np.uint8), mode=ColorMode.RGB)
    with pytest.raises(ContractError) as ei:
        img.validate()
    assert "3 channel" in ei.value.message


def test_alpha_shape_mismatch_rejected():
    img = ImageData(
        color=np.zeros((4, 4, 3), np.uint8),
        mode=ColorMode.RGB,
        alpha=np.zeros((4, 5), np.uint8),
    )
    with pytest.raises(ContractError) as ei:
        img.validate()
    assert "alpha" in ei.value.message


def test_premultiplied_requires_alpha():
    img = ImageData(
        color=np.zeros((4, 4, 3), np.uint8),
        mode=ColorMode.RGB,
        premultiplied=True,
    )
    with pytest.raises(ContractError) as ei:
        img.validate()
    assert "premultiplied" in ei.value.message


def test_channel_order_is_fixed():
    assert CHANNEL_ORDER[ColorMode.RGB] == ("R", "G", "B")
    assert CHANNEL_ORDER[ColorMode.CMYK] == ("C", "M", "Y", "K")
    assert CHANNEL_ORDER[ColorMode.GRAY] == ("L",)
    assert CHANNEL_ORDER[ColorMode.LAB] == ("L*", "a*", "b*")
