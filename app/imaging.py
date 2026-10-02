"""Image codec and contract validation for marker/mask pairs.

Boundary of trust: everything arriving over HTTP is decoded and checked
here before the numerical kernels see it.  Arrays handed to the kernels
are guaranteed to be 2-D, same shape, integer dtype.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, UnidentifiedImageError

from .schemas import FailureCategory

# Integer dtypes accepted after decoding.  PNG "I;16" maps to uint16;
# everything else is normalized to uint8 by Pillow mode "L".
_ACCEPTED_DTYPES = (np.dtype(np.uint8), np.dtype(np.uint16))


class ImageContractError(ValueError):
    """Raised when an uploaded image violates the data contract."""

    def __init__(self, category: FailureCategory, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


def decode_png(payload: bytes, *, label: str) -> np.ndarray:
    """Decode PNG bytes into a 2-D integer array.

    Raises ImageContractError on undecodable payloads or non-grayscale
    content (RGB / palette / alpha-bearing images are rejected, not
    silently converted, so callers know exactly what they sent).
    """
    try:
        with Image.open(io.BytesIO(payload)) as img:
            mode = img.mode
            if mode == "L":
                array = np.asarray(img, dtype=np.uint8)
            elif mode == "I;16":
                array = np.asarray(img, dtype=np.uint16)
            elif mode == "1":
                # 1-bit PNG: expand to 0/255 uint8 grayscale.
                array = np.asarray(img.convert("L"), dtype=np.uint8)
            else:
                raise ImageContractError(
                    FailureCategory.NOT_GRAYSCALE,
                    f"{label}: unsupported PNG mode {mode!r}; "
                    "expected 8-bit/16-bit grayscale or 1-bit bitmap",
                )
    except UnidentifiedImageError as exc:
        raise ImageContractError(
            FailureCategory.DECODE_ERROR, f"{label}: not a decodable PNG"
        ) from exc
    except ImageContractError:
        raise
    except Exception as exc:  # truncated/corrupt streams land here
        raise ImageContractError(
            FailureCategory.DECODE_ERROR, f"{label}: PNG decode failed: {exc}"
        ) from exc

    if array.ndim != 2:
        raise ImageContractError(
            FailureCategory.NOT_GRAYSCALE,
            f"{label}: expected a 2-D grayscale image, got ndim={array.ndim}",
        )
    return np.ascontiguousarray(array)


def encode_png(array: np.ndarray) -> bytes:
    """Encode a 2-D uint8/uint16 array as PNG bytes."""
    if array.ndim != 2:
        raise ValueError(f"can only encode 2-D arrays, got ndim={array.ndim}")
    if array.dtype == np.uint8:
        img = Image.fromarray(array, mode="L")
    elif array.dtype == np.uint16:
        img = Image.fromarray(array, mode="I;16")
    else:
        raise ValueError(f"unsupported dtype for PNG output: {array.dtype}")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def content_hash_prefix(array: np.ndarray) -> str:
    """Short content hash for log correlation (desensitized identifier)."""
    return hashlib.sha256(array.tobytes()).hexdigest()[:12]


@dataclass
class PairValidation:
    """Outcome of validating a marker/mask pair."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    failure_category: FailureCategory | None = None
    violation_pixels: int = 0


def validate_pair(
    marker: np.ndarray,
    mask: np.ndarray,
    *,
    max_side: int,
) -> PairValidation:
    """Check the joint contract of a marker/mask pair.

    Rules: same shape, integer dtype, side length within limits, and the
    geodesic constraint marker <= mask (counted, not enforced here — the
    caller decides between reject and clip).
    """
    reasons: list[str] = []

    if marker.shape != mask.shape:
        return PairValidation(
            ok=False,
            reasons=[
                f"shape mismatch: marker {marker.shape} vs mask {mask.shape}"
            ],
            failure_category=FailureCategory.SHAPE_MISMATCH,
        )

    for name, arr in (("marker", marker), ("mask", mask)):
        if arr.dtype not in _ACCEPTED_DTYPES:
            reasons.append(f"{name}: unsupported dtype {arr.dtype}")
        if max(arr.shape) > max_side:
            return PairValidation(
                ok=False,
                reasons=[
                    f"{name}: side {max(arr.shape)} exceeds limit {max_side}"
                ],
                failure_category=FailureCategory.IMAGE_TOO_LARGE,
            )
    if reasons:
        return PairValidation(
            ok=False,
            reasons=reasons,
            failure_category=FailureCategory.DECODE_ERROR,
        )

    violations = int(np.count_nonzero(marker > mask))
    if violations:
        return PairValidation(
            ok=False,
            reasons=[
                f"marker exceeds mask at {violations} pixel(s); "
                "geodesic dilation requires marker <= mask"
            ],
            failure_category=FailureCategory.MARKER_EXCEEDS_MASK,
            violation_pixels=violations,
        )

    if not np.any(marker):
        return PairValidation(
            ok=False,
            reasons=["marker is identically zero: nothing to reconstruct"],
            failure_category=FailureCategory.EMPTY_MARKER,
        )

    return PairValidation(ok=True)
