"""Image data contract: base64 PNG <-> float64 grayscale array.

The contract is enforced at the system boundary (contract: image data
contract module): 8-bit grayscale PNG (Pillow mode "L"), same-shape pair,
dimension limits from Settings. Violations raise ImageContractError,
which the API layer maps to HTTP 422 with a precise message.
"""

from __future__ import annotations

import base64
import binascii
from io import BytesIO

import numpy as np
from PIL import Image

from app.config import Settings


class ImageContractError(ValueError):
    """Raised when an inbound image violates the data contract."""


def decode_png_b64(payload: str, field: str, settings: Settings) -> np.ndarray:
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageContractError(f"{field}: not valid base64 ({exc})") from exc
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except Exception as exc:
        raise ImageContractError(f"{field}: not a decodable PNG ({exc})") from exc
    if img.format != "PNG":
        raise ImageContractError(f"{field}: expected PNG, got {img.format!r}")
    if img.mode != "L":
        raise ImageContractError(
            f"{field}: expected 8-bit grayscale (mode 'L'), got mode {img.mode!r}"
        )
    arr = np.asarray(img, dtype=np.float64)
    h, w = arr.shape
    for dim, name in ((h, "height"), (w, "width")):
        if not (settings.min_image_dim <= dim <= settings.max_image_dim):
            raise ImageContractError(
                f"{field}: {name} {dim} outside allowed range "
                f"[{settings.min_image_dim}, {settings.max_image_dim}]"
            )
    if not np.all(np.isfinite(arr)):
        raise ImageContractError(f"{field}: non-finite pixel values")
    return arr


def encode_png_b64(arr: np.ndarray) -> str:
    clipped = np.clip(np.rint(arr), 0, 255).astype(np.uint8)
    buf = BytesIO()
    Image.fromarray(clipped, mode="L").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def validate_pair(a: np.ndarray, b: np.ndarray) -> None:
    if a.shape != b.shape:
        raise ImageContractError(
            f"shape mismatch: image_a {a.shape} vs image_b {b.shape}; "
            "the contract requires a same-shape grayscale pair"
        )
