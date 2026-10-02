"""Pillow codec round-trips."""

from __future__ import annotations

import numpy as np
import pytest

from watershed_backend.imageio import (
    decode_gradient_png,
    decode_labels_png,
    encode_gradient_png,
    encode_labels_png,
)


def test_gradient_png_roundtrip_preserves_integer_values():
    gradient = np.array([[0, 100, 65535], [7, 42, 12345]], dtype=np.float64)
    decoded = decode_gradient_png(encode_gradient_png(gradient))
    np.testing.assert_array_equal(decoded, gradient)


def test_labels_png_roundtrip_with_offset():
    labels = np.array([[-1, 0, 1], [2, 0, -1]], dtype=np.int32)
    decoded = decode_labels_png(encode_labels_png(labels))
    np.testing.assert_array_equal(decoded, labels)


def test_labels_png_rejects_out_of_range():
    labels = np.array([[70000]], dtype=np.int32)
    with pytest.raises(ValueError):
        encode_labels_png(labels)
