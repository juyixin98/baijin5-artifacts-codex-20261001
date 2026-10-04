"""Lossless image (de)serialization.

Input images are decoded with Pillow from base64 PNG (mode ``1``/``L``/``P``;
RGB/RGBA are tolerated and reduced to luminance). Numeric outputs cannot be
represented losslessly by an ordinary grayscale PNG, so float32 distance
maps and int32 coordinate maps are serialized as **RGBA PNGs carrying the
raw 32-bit words** (little-endian bytes). This guarantees an exact byte
round-trip, including ``+inf``; :func:`decode_rgba_float32` /
:func:`decode_rgba_int32` invert it.
"""
from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image, UnidentifiedImageError


class ImageDecodeError(ValueError):
    """Raised when the payload cannot be decoded into a 2-D source mask."""


def decode_mask_b64(
    payload: str, source_value: str = "nonzero"
) -> np.ndarray:
    try:
        raw = base64.b64decode(payload, validate=True)
    except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
        raise ImageDecodeError("payload is not valid base64") from exc
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ImageDecodeError("payload is not a decodable PNG image") from exc

    if im.mode not in ("1", "L", "P", "RGB", "RGBA"):
        raise ImageDecodeError(f"unsupported image mode {im.mode!r}")
    gray = im.convert("L")
    arr = np.asarray(gray, dtype=np.uint8)

    if source_value == "nonzero":
        mask = arr > 0
    elif source_value == "white":
        mask = arr >= 128
    elif source_value == "black":
        mask = arr < 128
    else:  # pragma: no cover - validated at the contract layer
        raise ImageDecodeError(f"unknown source_value {source_value!r}")
    return mask


def _words_to_rgba_png(words: np.ndarray) -> str:
    if words.dtype not in (np.float32, np.int32):
        raise TypeError("words must be float32 or int32")
    h, w = words.shape
    raw = words.astype("<f4" if words.dtype == np.float32 else "<i4", copy=False)
    rgba = raw.view(np.uint8).reshape(h, w, 4)
    # Pillow wants contiguous uint8 HxWx4.
    rgba = np.ascontiguousarray(rgba)
    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=False)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def encode_float32_png_b64(values: np.ndarray) -> str:
    return _words_to_rgba_png(np.asarray(values, dtype=np.float32))


def encode_int32_png_b64(values: np.ndarray) -> str:
    return _words_to_rgba_png(np.asarray(values, dtype=np.int32))


def decode_rgba_float32(payload: str) -> np.ndarray:
    raw = base64.b64decode(payload, validate=True)
    im = Image.open(io.BytesIO(raw))
    if im.mode != "RGBA":
        raise ImageDecodeError("expected an RGBA-encoded float32 PNG")
    rgba = np.asarray(im, dtype=np.uint8)
    return rgba.reshape(rgba.shape[0], rgba.shape[1], 4).copy().view("<f4")[..., 0]


def decode_rgba_int32(payload: str) -> np.ndarray:
    raw = base64.b64decode(payload, validate=True)
    im = Image.open(io.BytesIO(raw))
    if im.mode != "RGBA":
        raise ImageDecodeError("expected an RGBA-encoded int32 PNG")
    rgba = np.asarray(im, dtype=np.uint8)
    return rgba.reshape(rgba.shape[0], rgba.shape[1], 4).copy().view("<i4")[..., 0]


def encode_mask_png_b64(mask: np.ndarray) -> str:
    """Convenience: encode a boolean mask as a 1-bit PNG."""
    buf = io.BytesIO()
    Image.fromarray(np.asarray(mask, dtype=np.uint8) * 255, mode="L").save(
        buf, format="PNG", optimize=True
    )
    return base64.b64encode(buf.getvalue()).decode("ascii")
