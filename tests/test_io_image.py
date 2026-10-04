"""Image codec round-trip tests."""
from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from PIL import Image

from app.io_image import (
    ImageDecodeError,
    decode_mask_b64,
    decode_rgba_float32,
    decode_rgba_int32,
    encode_float32_png_b64,
    encode_int32_png_b64,
    encode_mask_png_b64,
)


def _b64_png(arr: np.ndarray, mode: str) -> str:
    buf = io.BytesIO()
    Image.fromarray(arr, mode=mode).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_decode_modes_nonzero():
    mask = np.array([[0, 255], [128, 0]], dtype=np.uint8)
    payload = _b64_png(mask, "L")
    out = decode_mask_b64(payload, "nonzero")
    assert np.array_equal(out, np.array([[False, True], [True, False]]))


def test_decode_white_black_threshold():
    arr = np.array([[0, 200], [127, 128]], dtype=np.uint8)
    payload = _b64_png(arr, "L")
    assert decode_mask_b64(payload, "white").tolist() == [
        [False, True], [False, True]
    ]
    assert decode_mask_b64(payload, "black").tolist() == [
        [True, False], [True, False]
    ]


def test_decode_rejects_bad_base64():
    with pytest.raises(ImageDecodeError, match="base64"):
        decode_mask_b64("@@@not-base64@@@")


def test_decode_rejects_non_png():
    raw = base64.b64encode(b"this is definitely not a png").decode()
    with pytest.raises(ImageDecodeError, match="PNG"):
        decode_mask_b64(raw)


def test_float32_roundtrip_preserves_inf_and_values():
    values = np.array(
        [[0.0, 1.5, np.inf], [np.sqrt(2), -0.0, 3.25e7]], dtype=np.float32
    )
    payload = encode_float32_png_b64(values)
    back = decode_rgba_float32(payload)
    assert back.shape == values.shape
    assert np.array_equal(back, values, equal_nan=True)
    assert np.isinf(back[0, 2])


def test_int32_roundtrip_preserves_coordinates():
    values = np.array([[-1, 0, 2147483647], [19, 2026, -1]], dtype=np.int32)
    payload = encode_int32_png_b64(values)
    back = decode_rgba_int32(payload)
    assert np.array_equal(back, values)


def test_mask_png_roundtrip():
    mask = np.array([[True, False], [False, True], [True, True]])
    payload = encode_mask_png_b64(mask)
    back = decode_mask_b64(payload)
    assert np.array_equal(back, mask)
