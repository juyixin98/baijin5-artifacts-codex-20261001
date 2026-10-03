"""Image data contract.

A request is a (marker, mask) pair of 2-D finite numeric images of identical
shape with ``marker <= mask`` everywhere. Inputs that violate the contract are
either rejected with a categorized :class:`ContractViolation` or — when the
caller explicitly chooses ``on_violation="clip"`` — the marker is clipped to
the mask and the clip is reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .errors import ContractViolation

#: Working dtype for all numerical work.
WORK_DTYPE = np.float64


@dataclass(frozen=True)
class ImagePair:
    """A validated marker/mask pair."""

    marker: np.ndarray  # WORK_DTYPE, 2-D, finite, <= mask
    mask: np.ndarray  # WORK_DTYPE, 2-D, finite
    clipped: bool  # True iff the marker was clipped to satisfy marker <= mask


def coerce_image(name: str, value: Any, *, max_pixels: int) -> np.ndarray:
    """Coerce user input (nested lists or ndarray) into a valid 2-D image."""
    try:
        arr = np.asarray(value)
    except Exception as exc:  # ragged lists etc.
        raise ContractViolation("MALFORMED_IMAGE", f"{name}: cannot be read as an array ({exc})") from exc

    if arr.ndim != 2:
        raise ContractViolation("BAD_RANK", f"{name}: expected a 2-D image, got {arr.ndim}-D")
    if arr.size == 0 or 0 in arr.shape:
        raise ContractViolation("EMPTY_IMAGE", f"{name}: image has zero pixels, shape={arr.shape}")
    if arr.size > max_pixels:
        raise ContractViolation(
            "IMAGE_TOO_LARGE", f"{name}: {arr.size} pixels exceeds limit of {max_pixels}"
        )
    if not (
        arr.dtype == np.bool_
        or np.issubdtype(arr.dtype, np.integer)
        or np.issubdtype(arr.dtype, np.floating)
    ):
        raise ContractViolation("DTYPE_UNSUPPORTED", f"{name}: unsupported dtype {arr.dtype}")

    arr = arr.astype(WORK_DTYPE)
    if not np.all(np.isfinite(arr)):
        raise ContractViolation("NON_FINITE", f"{name}: contains NaN or infinite values")
    return arr


def validate_pair(
    marker: Any,
    mask: Any,
    *,
    on_violation: str = "reject",
    max_pixels: int = 4_000_000,
) -> ImagePair:
    """Validate a marker/mask pair against the contract.

    Raises ContractViolation on rejection; returns ImagePair(clipped=True)
    when the marker exceeded the mask and was explicitly clipped.
    """
    if on_violation not in ("reject", "clip"):
        raise ContractViolation("BAD_POLICY", f"on_violation must be 'reject' or 'clip', got {on_violation!r}")

    m = coerce_image("marker", marker, max_pixels=max_pixels)
    g = coerce_image("mask", mask, max_pixels=max_pixels)

    if m.shape != g.shape:
        raise ContractViolation(
            "SHAPE_MISMATCH", f"marker shape {m.shape} != mask shape {g.shape}"
        )

    excess = m > g
    clipped = False
    if excess.any():
        count = int(excess.sum())
        max_over = float((m - g)[excess].max())
        if on_violation == "reject":
            raise ContractViolation(
                "MARKER_EXCEEDS_MASK",
                f"marker exceeds mask at {count} pixel(s), worst excess {max_over:g}",
            )
        m = np.minimum(m, g)
        clipped = True

    return ImagePair(marker=m, mask=g, clipped=clipped)
