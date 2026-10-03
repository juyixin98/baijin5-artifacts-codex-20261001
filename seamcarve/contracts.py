"""Image data contract.

Defines what a legal image / protection mask looks like, converts the
JSON-transport representation (nested lists) into validated numpy arrays,
and holds the result types shared across the pipeline.

Contract rules
--------------
Image pixels:
  * nested lists, rectangular, either HxW (grayscale) or HxWx3 (RGB)
  * integer values in [0, 255]
  * H >= 1, W >= 1
Protection mask:
  * nested lists of 0/1, exactly HxW matching the image
Seam path:
  * one column per row, adjacent rows differ by at most kernel.MAX_STEP
  * reported in ORIGINAL image coordinates (see carving.col_map)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np

from .errors import ContractViolationError


def validate_pixels(pixels: object) -> np.ndarray:
    """Validate nested-list pixels and return a uint8 array (HxW or HxWx3)."""
    if isinstance(pixels, np.ndarray):
        arr = pixels
    else:
        if not isinstance(pixels, (list, tuple)) or len(pixels) == 0:
            raise ContractViolationError("pixels must be a non-empty nested list")
        try:
            arr = np.asarray(pixels)
        except (ValueError, TypeError) as exc:
            raise ContractViolationError(f"pixels are not rectangular: {exc}") from exc
    if arr.dtype == object:
        raise ContractViolationError("pixels are not rectangular (ragged rows)")
    if arr.ndim not in (2, 3):
        raise ContractViolationError(
            f"pixels must be HxW or HxWx3, got ndim={arr.ndim}"
        )
    if arr.ndim == 3 and arr.shape[2] != 3:
        raise ContractViolationError(
            f"RGB images must have exactly 3 channels, got {arr.shape[2]}"
        )
    if arr.shape[0] < 1 or arr.shape[1] < 1:
        raise ContractViolationError("image must be at least 1x1")
    if not np.issubdtype(arr.dtype, np.integer):
        raise ContractViolationError(f"pixels must be integers, got {arr.dtype}")
    if arr.size and (int(arr.min()) < 0 or int(arr.max()) > 255):
        raise ContractViolationError("pixel values must be within [0, 255]")
    return arr.astype(np.uint8)


def validate_protect_mask(mask: object, shape: tuple[int, int]) -> np.ndarray:
    """Validate a 0/1 nested-list mask against the image shape -> bool array."""
    if mask is None:
        return np.zeros(shape, dtype=bool)
    if isinstance(mask, np.ndarray):
        arr = mask
    else:
        try:
            arr = np.asarray(mask)
        except (ValueError, TypeError) as exc:
            raise ContractViolationError(f"protect_mask is not rectangular: {exc}") from exc
    if arr.dtype == object:
        raise ContractViolationError("protect_mask is not rectangular (ragged rows)")
    if arr.shape != tuple(shape):
        raise ContractViolationError(
            f"protect_mask shape {tuple(arr.shape)} does not match image shape {tuple(shape)}"
        )
    values = np.unique(arr)
    if not set(values.tolist()).issubset({0, 1, False, True}):
        raise ContractViolationError("protect_mask values must be 0 or 1")
    return arr.astype(bool)


def image_sha256(image: np.ndarray) -> str:
    """Stable content hash of an image, used to tie logs to inputs."""
    h = hashlib.sha256()
    h.update(str(image.shape).encode())
    h.update(image.dtype.str.encode())
    h.update(np.ascontiguousarray(image).tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class SeamPath:
    """One removed/found seam, in ORIGINAL image coordinates."""

    points: tuple[tuple[int, int], ...]  # (row, original_col) per row, top to bottom
    energy: float
    energy_mode: str
    width_after: int  # image width after this seam is removed (== current width when only found)

    def columns(self) -> list[int]:
        return [c for _, c in self.points]


@dataclass(frozen=True)
class CarveReport:
    """Result of a multi-seam carve job."""

    seams: tuple[SeamPath, ...]
    energy_mode: str
    original_shape: tuple[int, int]
    final_width: int
    chunks: tuple[dict, ...] = field(default_factory=tuple)
