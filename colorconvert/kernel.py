"""Numeric kernel: ICC color conversion via LittleCMS (Pillow ImageCms).

Responsibilities of this module — and only these:

* apply the fixed alpha semantics (alpha is never transformed; color is
  always converted in non-premultiplied form);
* run the per-pixel ICC transform on a color array;
* report exactly what was done (intent, black-point compensation, profiles,
  engine version) and whether the conversion is potentially lossy.

This module never claims cross-gamut conversions are lossless.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageCms

from .contract import ColorMode, ImageData
from .errors import EngineError, FailureCategory
from .profiles import ProfileHandle

ENGINE_NAME = "LittleCMS"
ENGINE_VERSION = ImageCms.core.littlecms_version


class RenderingIntent(enum.Enum):
    PERCEPTUAL = 0
    RELATIVE_COLORIMETRIC = 1
    SATURATION = 2
    ABSOLUTE_COLORIMETRIC = 3

    @classmethod
    def parse(cls, value: str) -> "RenderingIntent":
        key = value.strip().lower().replace("-", "_")
        try:
            return cls[key.upper()]
        except KeyError:
            from .errors import ConversionError

            raise ConversionError(
                FailureCategory.UNSUPPORTED_INTENT,
                f"unknown rendering intent {value!r}; expected one of "
                + ", ".join(i.name.lower() for i in cls),
            ) from None


@dataclass(frozen=True)
class KernelReport:
    """What the kernel actually did.  Embedded in every job record."""

    source_profile_id: str
    source_profile_sha256_12: str
    target_profile_id: str
    target_profile_sha256_12: str
    rendering_intent: str
    black_point_compensation: bool
    in_mode: str
    out_mode: str
    engine: str
    lossy: bool
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_report(
    src: ProfileHandle,
    dst: ProfileHandle,
    intent: RenderingIntent,
    bpc: bool,
) -> KernelReport:
    """Describe a conversion without running it (never claims lossless)."""
    lossy = src.sha256 != dst.sha256
    notes: list[str] = []
    if lossy:
        notes.append(
            "跨 profile 转换不保证无损：色域映射与 8 位量化会引入误差"
        )
    if dst.color_space == "CMYK":
        notes.append("目标为 CMYK 输出色域，超出色域的颜色会被裁剪/压缩")
    return KernelReport(
        source_profile_id=src.profile_id,
        source_profile_sha256_12=src.fingerprint,
        target_profile_id=dst.profile_id,
        target_profile_sha256_12=dst.fingerprint,
        rendering_intent=intent.name.lower(),
        black_point_compensation=bpc,
        in_mode=src.color_mode.name,
        out_mode=dst.color_mode.name,
        engine=f"{ENGINE_NAME} {ENGINE_VERSION}",
        lossy=lossy,
        notes=tuple(notes),
    )


def _unpremultiply(color: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """color_straight = round(color_premul * 255 / alpha); alpha==0 -> 0."""
    a = alpha.astype(np.float64)[..., None]
    c = color.astype(np.float64)
    out = np.zeros_like(c)
    np.divide(c * 255.0, a, out=out, where=a > 0)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _premultiply(color: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    a = alpha.astype(np.float64)[..., None]
    return np.clip(np.rint(color.astype(np.float64) * a / 255.0), 0, 255).astype(
        np.uint8
    )


class ColorKernel:
    """Converts color arrays between validated ICC profiles."""

    def __init__(self) -> None:
        self._transforms: dict[tuple, ImageCms.ImageCmsTransform] = {}

    def _transform(
        self,
        src: ProfileHandle,
        dst: ProfileHandle,
        intent: RenderingIntent,
        bpc: bool,
    ) -> ImageCms.ImageCmsTransform:
        key = (
            src.sha256,
            dst.sha256,
            src.color_mode.value,
            dst.color_mode.value,
            intent.value,
            bpc,
        )
        transform = self._transforms.get(key)
        if transform is None:
            flags = ImageCms.FLAGS["BLACKPOINTCOMPENSATION"] if bpc else 0
            try:
                transform = ImageCms.buildTransformFromOpenProfiles(
                    src.cms_profile,
                    dst.cms_profile,
                    src.color_mode.value,
                    dst.color_mode.value,
                    renderingIntent=intent.value,
                    flags=flags,
                )
            except Exception as exc:
                raise EngineError(
                    FailureCategory.ENGINE_ERROR,
                    f"LittleCMS could not build transform "
                    f"{src.profile_id} -> {dst.profile_id}: {exc}",
                ) from exc
            self._transforms[key] = transform
        return transform

    def convert_color_array(
        self,
        color: np.ndarray,
        src: ProfileHandle,
        dst: ProfileHandle,
        intent: RenderingIntent,
        bpc: bool,
    ) -> np.ndarray:
        """Convert one ``(H, W, C)`` uint8 color array.  No alpha logic here."""
        transform = self._transform(src, dst, intent, bpc)
        in_mode = src.color_mode
        h, w = color.shape[:2]
        if in_mode is ColorMode.GRAY:
            pil_in = Image.fromarray(color[:, :, 0], mode="L")
        elif in_mode is ColorMode.LAB:
            # Pillow's LAB array interface disagrees with its transform
            # byte convention; frombytes/tobytes is the consistent path.
            pil_in = Image.frombytes("LAB", (w, h), color.tobytes())
        else:
            pil_in = Image.fromarray(color, mode=in_mode.value)
        try:
            pil_out = ImageCms.applyTransform(pil_in, transform)
        except Exception as exc:
            raise EngineError(
                FailureCategory.ENGINE_ERROR,
                f"LittleCMS transform failed: {exc}",
            ) from exc
        if dst.color_mode is ColorMode.LAB:
            out = np.frombuffer(pil_out.tobytes(), dtype=np.uint8).reshape(
                pil_out.height, pil_out.width, 3
            )
            return out.copy()
        out = np.asarray(pil_out, dtype=np.uint8)
        if dst.color_mode is ColorMode.GRAY:
            out = out[:, :, None]
        return out.copy()

    def convert(
        self,
        image: ImageData,
        src: ProfileHandle,
        dst: ProfileHandle,
        intent: RenderingIntent,
        bpc: bool,
    ) -> tuple[ImageData, KernelReport]:
        """Convert a full image honoring the fixed alpha semantics."""
        image.validate()

        color = image.color
        if image.alpha is not None and image.premultiplied:
            color = _unpremultiply(color, image.alpha)

        converted = self.convert_color_array(color, src, dst, intent, bpc)

        if image.alpha is not None and image.premultiplied:
            converted = _premultiply(converted, image.alpha)

        out_mode = dst.color_mode
        result = ImageData(
            color=converted,
            mode=out_mode,
            # Alpha is passed through untouched, bit-identical.
            alpha=image.alpha,
            premultiplied=image.premultiplied,
        )
        return result, build_report(src, dst, intent, bpc)
