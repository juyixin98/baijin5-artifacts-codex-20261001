"""Thin numeric kernel over the littleCMS engine (via Pillow ImageCms).

This module is the only place that talks to the ICC engine.  It works on
plain ``uint8`` color arrays (no alpha - alpha is handled separately) and
never invents profiles: both endpoint profiles are mandatory byte strings
that have already passed validation.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageCms

from ..contract.enums import ColorSpace, RenderingIntent
from ..errors import EngineFailureError, IntentUnsupportedError

_PIL_MODE = {
    ColorSpace.RGB: "RGB",
    ColorSpace.GRAY: "L",
    ColorSpace.CMYK: "CMYK",
}

# littleCMS intent directions (cmsFLAGS / lcms2.h INTENT_*)
_DIRECTION_INPUT = 0
_DIRECTION_OUTPUT = 1


def _open_profile(data: bytes) -> ImageCms.ImageCmsProfile:
    return ImageCms.getOpenProfile(io.BytesIO(data))


def check_intent_supported(
    src_profile: bytes, dst_profile: bytes, intent: RenderingIntent
) -> None:
    """Reject intents the engine reports as unsupported by either endpoint."""
    src = _open_profile(src_profile).profile
    dst = _open_profile(dst_profile).profile
    if not src.is_intent_supported(int(intent), _DIRECTION_INPUT):
        raise IntentUnsupportedError(
            "rendering intent not supported by the source profile",
            detail={"intent": RenderingIntent(intent).name, "direction": "input"},
        )
    if not dst.is_intent_supported(int(intent), _DIRECTION_OUTPUT):
        raise IntentUnsupportedError(
            "rendering intent not supported by the target profile",
            detail={"intent": RenderingIntent(intent).name, "direction": "output"},
        )


def build_transform(
    src_profile: bytes,
    dst_profile: bytes,
    src_space: ColorSpace,
    dst_space: ColorSpace,
    intent: RenderingIntent,
    black_point_compensation: bool,
) -> ImageCms.ImageCmsTransform:
    flags = ImageCms.FLAGS["BLACKPOINTCOMPENSATION"] if black_point_compensation else 0
    try:
        return ImageCms.buildTransformFromOpenProfiles(
            _open_profile(src_profile),
            _open_profile(dst_profile),
            _PIL_MODE[src_space],
            _PIL_MODE[dst_space],
            renderingIntent=int(intent),
            flags=flags,
        )
    except Exception as exc:
        raise EngineFailureError(
            "ICC engine refused to build the transform",
            detail={"engine_error": str(exc)[:200]},
        ) from exc


def _to_pil(color: np.ndarray, space: ColorSpace) -> Image.Image:
    if space is ColorSpace.GRAY:
        return Image.fromarray(color[..., 0], mode="L")
    return Image.fromarray(color, mode=_PIL_MODE[space])


def _from_pil(image: Image.Image) -> np.ndarray:
    arr = np.asarray(image, dtype=np.uint8)
    if arr.ndim == 2:  # single-channel output (e.g. gray) comes back as (H, W)
        arr = arr[..., None]
    return np.ascontiguousarray(arr)


def apply_transform(
    transform: ImageCms.ImageCmsTransform, color: np.ndarray, space: ColorSpace
) -> np.ndarray:
    """Apply a built transform to a color-only uint8 array."""
    try:
        out = ImageCms.applyTransform(_to_pil(color, space), transform)
    except Exception as exc:
        raise EngineFailureError(
            "ICC engine failed while applying the transform",
            detail={"engine_error": str(exc)[:200]},
        ) from exc
    return _from_pil(out)


def convert_color(
    color: np.ndarray,
    src_profile: bytes,
    dst_profile: bytes,
    src_space: ColorSpace,
    dst_space: ColorSpace,
    intent: RenderingIntent,
    black_point_compensation: bool,
) -> np.ndarray:
    """One-shot convenience: build the transform and apply it."""
    transform = build_transform(
        src_profile, dst_profile, src_space, dst_space, intent, black_point_compensation
    )
    return apply_transform(transform, color, src_space)
