"""Image byte <-> ImageDocument codecs (PNG / TIFF via Pillow)."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from ..contract.enums import AlphaMode, ColorSpace
from ..contract.image import ImageDocument
from ..errors import ContractViolationError

_MODE_MAP: dict[str, tuple[ColorSpace, bool]] = {
    "RGB": (ColorSpace.RGB, False),
    "RGBA": (ColorSpace.RGB, True),
    "L": (ColorSpace.GRAY, False),
    "LA": (ColorSpace.GRAY, True),
    "CMYK": (ColorSpace.CMYK, False),
}


def decode_image(data: bytes, alpha_mode_in: AlphaMode) -> ImageDocument:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ContractViolationError(
            "image bytes cannot be decoded", detail={"error": str(exc)[:160]}
        ) from exc
    if img.mode not in _MODE_MAP:
        raise ContractViolationError(
            "unsupported image mode; use RGB/RGBA/L/LA/CMYK",
            detail={"mode": img.mode},
        )
    color_space, has_alpha = _MODE_MAP[img.mode]
    alpha_mode = alpha_mode_in if has_alpha else AlphaMode.NONE
    pixels = np.asarray(img, dtype=np.uint8)
    if pixels.ndim == 2:
        pixels = pixels[..., None]
    embedded = img.info.get("icc_profile") or None
    return ImageDocument(
        pixels=np.ascontiguousarray(pixels),
        color_space=color_space,
        alpha_mode=alpha_mode,
        embedded_profile=embedded,
    )


def encode_image(doc: ImageDocument, fmt: str) -> bytes:
    mode_by_space = {
        (ColorSpace.RGB, False): "RGB",
        (ColorSpace.RGB, True): "RGBA",
        (ColorSpace.GRAY, False): "L",
        (ColorSpace.GRAY, True): "LA",
        (ColorSpace.CMYK, False): "CMYK",
    }
    mode = mode_by_space[(doc.color_space, doc.has_alpha)]
    if doc.color_space is ColorSpace.GRAY and not doc.has_alpha:
        img = Image.fromarray(doc.pixels[..., 0], mode="L")
    else:
        img = Image.fromarray(doc.pixels, mode=mode)
    if doc.color_space is ColorSpace.CMYK and fmt == "png":
        raise ContractViolationError("CMYK output requires TIFF, not PNG")
    buf = io.BytesIO()
    save_kwargs = {}
    if doc.embedded_profile:
        save_kwargs["icc_profile"] = doc.embedded_profile
    img.save(buf, format="PNG" if fmt == "png" else "TIFF", **save_kwargs)
    return buf.getvalue()
