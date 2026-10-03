"""Image data contract: request/response models and payload codecs.

Wire format is a consistent envelope:

* success — ``{"ok": true, "data": {...}}``
* failure — ``{"ok": false, "error": {"category", "message", "details", "run_id"}}``

Images travel as base64-encoded PNG/JPEG bytes; protection masks as
base64-encoded images (any non-zero pixel = protected) and/or explicit
rectangles.  All decoding failures raise domain errors with stable
categories — never bare 500s.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from typing import Literal

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from app.config import VALID_ENERGY_MODES
from app.errors import InvalidImageError, InvalidRequestError, MaskShapeError


class ProtectRect(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    height: int = Field(ge=1)
    width: int = Field(ge=1)


class ImagePayload(BaseModel):
    image_b64: str = Field(min_length=8)
    protect_mask_b64: str | None = None
    protect_rects: list[ProtectRect] = Field(default_factory=list)


class EnergyOptions(BaseModel):
    energy_mode: Literal["gradient", "forward"] | None = None
    max_displacement: int | None = Field(default=None, ge=1, le=8)


class SeamRequest(ImagePayload, EnergyOptions):
    pass


class CarveRequest(ImagePayload, EnergyOptions):
    num_seams: int = Field(ge=1, le=4096)


class JobRequest(CarveRequest):
    chunk_size: int | None = Field(default=None, ge=1, le=512)


def _decode_b64(data: str, what: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidImageError(
            f"{what} is not valid base64", details={"error": str(exc)}
        ) from exc


def decode_image(image_b64: str) -> tuple[np.ndarray, str]:
    """Decode a base64 image into a 2-D (grayscale) or 3-D (RGB) array.

    Returns (array, sha256_of_raw_bytes).
    """
    raw = _decode_b64(image_b64, "image_b64")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.mode == "L":
                arr = np.asarray(img, dtype=np.uint8).copy()
            elif img.mode == "RGB":
                arr = np.asarray(img, dtype=np.uint8).copy()
            else:
                arr = np.asarray(img.convert("RGB"), dtype=np.uint8).copy()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError(
            "image_b64 does not decode to a readable image",
            details={"error": str(exc)},
        ) from exc
    if arr.size == 0:
        raise InvalidImageError("decoded image is empty")
    return arr, digest


def decode_mask(payload: ImagePayload, shape: tuple[int, int]) -> np.ndarray:
    """Combine mask image and protection rectangles into a boolean mask."""
    mask = np.zeros(shape, dtype=bool)
    if payload.protect_mask_b64 is not None:
        raw = _decode_b64(payload.protect_mask_b64, "protect_mask_b64")
        try:
            with Image.open(io.BytesIO(raw)) as img:
                mask_arr = np.asarray(img.convert("L"))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise InvalidImageError(
                "protect_mask_b64 does not decode to a readable image",
                details={"error": str(exc)},
            ) from exc
        if mask_arr.shape != shape:
            raise MaskShapeError(
                "protect mask shape must match the image",
                details={
                    "image_shape": list(shape),
                    "mask_shape": list(mask_arr.shape),
                },
            )
        mask |= mask_arr > 0
    for rect in payload.protect_rects:
        r0, r1 = rect.row, rect.row + rect.height
        c0, c1 = rect.col, rect.col + rect.width
        if r1 > shape[0] or c1 > shape[1]:
            raise MaskShapeError(
                "protection rectangle exceeds image bounds",
                details={
                    "rect": rect.model_dump(),
                    "image_shape": list(shape),
                },
            )
        mask[r0:r1, c0:c1] = True
    return mask


def encode_image(arr: np.ndarray) -> str:
    """Encode a 2-D/3-D uint8 array as base64 PNG."""
    array = np.asarray(arr)
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    img = Image.fromarray(array)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def seam_record_to_dict(record) -> dict:
    return {
        "index": record.index,
        "path_original_cols": list(record.path_original_cols),
        "energy": record.energy,
        "mode": record.mode,
        "width_before": record.width_before,
        "width_after": record.width_after,
    }


def validate_num_seams(num_seams: int, width: int, min_remaining: int) -> None:
    if num_seams > width - min_remaining:
        raise InvalidRequestError(
            "num_seams exceeds removable width",
            details={
                "num_seams": num_seams,
                "width": width,
                "min_remaining_width": min_remaining,
            },
        )


def resolve_options(options: EnergyOptions, defaults) -> tuple[str, int]:
    mode = options.energy_mode or defaults.energy_mode
    disp = options.max_displacement or defaults.max_displacement
    if mode not in VALID_ENERGY_MODES:  # pragma: no cover - pydantic guards
        raise InvalidRequestError("unknown energy_mode", details={"mode": mode})
    return mode, disp
