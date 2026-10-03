"""Image data contract.

Every image entering the pipeline is normalized to a 2-D uint8 array of
0 (background) / 1 (foreground). Anything that cannot be normalized
unambiguously is rejected with a categorized ContractError so callers can
distinguish failure classes instead of getting a bare 500.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

MIN_SIDE = 3


class ContractError(ValueError):
    """Input violates the image contract. `category` is machine-readable."""

    def __init__(self, category: str, detail: str):
        super().__init__(f"{category}: {detail}")
        self.category = category
        self.detail = detail


def validate_binary_image(arr: np.ndarray) -> np.ndarray:
    """Normalize an array-like to the 0/1 uint8 contract or raise."""
    a = np.asarray(arr)
    if a.ndim != 2:
        raise ContractError("NOT_2D", f"expected a 2-D image, got shape {a.shape}")
    if a.shape[0] < MIN_SIDE or a.shape[1] < MIN_SIDE:
        raise ContractError(
            "TOO_SMALL", f"image must be at least {MIN_SIDE}x{MIN_SIDE}, got {a.shape}"
        )
    unique = np.unique(a)
    if not set(unique.tolist()) <= {0, 1}:
        raise ContractError(
            "NOT_BINARY",
            f"pixel values must be 0/1, found {len(unique)} distinct values "
            f"(min={unique.min()}, max={unique.max()})",
        )
    return a.astype(np.uint8)


def image_from_png_bytes(data: bytes) -> np.ndarray:
    """Decode a strictly binary PNG (values 0 and 255 only) to the contract."""
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
    except Exception as exc:  # PIL raises many types; normalize to contract error
        raise ContractError("DECODE_FAILED", f"not a decodable image: {exc}") from exc
    a = np.asarray(img)
    unique = set(np.unique(a).tolist())
    if unique <= {0, 1}:
        pass  # already 0/1
    elif unique <= {0, 255}:
        a = (a > 0).astype(np.uint8)
    else:
        raise ContractError(
            "NOT_BINARY",
            f"PNG must be strictly binary (0/255), found {len(unique)} distinct gray levels; "
            "threshold it before uploading",
        )
    return validate_binary_image(a)
