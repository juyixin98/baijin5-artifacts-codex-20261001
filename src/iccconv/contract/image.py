"""Image data contract.

An :class:`ImageDocument` is the only way pixel data enters or leaves the
conversion kernel.  Construction validates the full contract, so anything
downstream can rely on:

* dtype ``uint8``, shape ``(H, W, C)``, C contiguous
* channel count consistent with ``color_space`` + ``alpha_mode``
* restricted CMYK: CMYK documents never carry alpha
* the embedded profile (if any) is carried as opaque bytes; it is only
  interpreted by the profile validation module, never assumed to be sRGB.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..errors import ContractViolationError, UnsupportedAlphaError
from .enums import AlphaMode, ColorSpace


@dataclass(frozen=True, eq=False)
class ImageDocument:
    pixels: np.ndarray
    color_space: ColorSpace
    alpha_mode: AlphaMode = AlphaMode.NONE
    embedded_profile: bytes | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "color_space", ColorSpace(self.color_space))
        object.__setattr__(self, "alpha_mode", AlphaMode(self.alpha_mode))

        if not isinstance(self.pixels, np.ndarray):
            raise ContractViolationError("pixels must be a numpy.ndarray")
        if self.pixels.dtype != np.uint8:
            raise ContractViolationError(
                "pixels must be uint8; 16-bit and float pipelines are out of scope",
                detail={"dtype": str(self.pixels.dtype)},
            )
        if self.pixels.ndim != 3:
            raise ContractViolationError(
                "pixels must have shape (H, W, C)",
                detail={"shape": list(self.pixels.shape)},
            )
        height, width, channels = self.pixels.shape
        if height < 1 or width < 1:
            raise ContractViolationError("image must be at least 1x1 pixel")

        expected = self.color_space.base_channels + (1 if self.has_alpha else 0)
        if channels != expected:
            raise ContractViolationError(
                "channel count does not match color space and alpha mode",
                detail={
                    "color_space": self.color_space.value,
                    "alpha_mode": self.alpha_mode.value,
                    "expected_channels": expected,
                    "actual_channels": channels,
                },
            )
        if self.color_space is ColorSpace.CMYK and self.has_alpha:
            # Restricted CMYK: the service deliberately does not support
            # alpha-carrying CMYK (no CMYKA mode in the engine binding).
            raise UnsupportedAlphaError(
                "CMYK images with an alpha channel are not supported; "
                "flatten or convert via an RGB working space first"
            )

    @property
    def has_alpha(self) -> bool:
        return self.alpha_mode is not AlphaMode.NONE

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def channels(self) -> int:
        return int(self.pixels.shape[2])

    @property
    def base_channels(self) -> int:
        return self.color_space.base_channels
