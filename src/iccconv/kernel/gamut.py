"""Out-of-gamut estimation.

There is no exact per-pixel "out of gamut" oracle available from the
engine binding, so this module implements a documented heuristic:

    pixel p is flagged  <=>  max_c |p_c - roundtrip(p)_c| > tolerance

where roundtrip = inverse(forward) with a relative-colorimetric inverse
transform.  In-gamut colors round-trip within quantization noise, while
colors clipped by gamut mapping come back shifted.  The result is an
estimate, never a proof; callers must treat the counts as heuristic.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..contract.enums import ColorSpace, RenderingIntent
from .transform import apply_transform, build_transform


@dataclass(frozen=True)
class GamutReport:
    method: str
    tolerance: int
    flagged_pixels: int
    total_pixels: int
    certainty: str = "heuristic"

    @property
    def flagged_fraction(self) -> float:
        return self.flagged_pixels / self.total_pixels if self.total_pixels else 0.0


def roundtrip_gamut_mask(
    color: np.ndarray,
    src_profile: bytes,
    dst_profile: bytes,
    src_space: ColorSpace,
    dst_space: ColorSpace,
    intent: RenderingIntent,
    black_point_compensation: bool,
    tolerance: int,
) -> np.ndarray:
    """Boolean (H, W) mask: True where the pixel is estimated out-of-gamut."""
    forward = build_transform(
        src_profile, dst_profile, src_space, dst_space, intent, black_point_compensation
    )
    inverse = build_transform(
        dst_profile,
        src_profile,
        dst_space,
        src_space,
        RenderingIntent.RELATIVE_COLORIMETRIC,
        False,
    )
    there = apply_transform(forward, color, src_space)
    back = apply_transform(inverse, there, dst_space)
    error = np.abs(back.astype(np.int16) - color.astype(np.int16)).max(axis=-1)
    return error > tolerance


def estimate_gamut(
    color: np.ndarray,
    src_profile: bytes,
    dst_profile: bytes,
    src_space: ColorSpace,
    dst_space: ColorSpace,
    intent: RenderingIntent,
    black_point_compensation: bool,
    tolerance: int,
) -> GamutReport:
    mask = roundtrip_gamut_mask(
        color,
        src_profile,
        dst_profile,
        src_space,
        dst_space,
        intent,
        black_point_compensation,
        tolerance,
    )
    return GamutReport(
        method="roundtrip-heuristic",
        tolerance=tolerance,
        flagged_pixels=int(mask.sum()),
        total_pixels=int(mask.size),
    )
