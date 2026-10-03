"""Image data contract.

This module is the single source of truth for how image data is represented
inside the backend:

* ``color`` is a ``numpy.uint8`` array of shape ``(H, W, C)``, channels-last,
  with the channel order fixed by :data:`CHANNEL_ORDER`.
* ``alpha`` is a *separate* ``numpy.uint8`` plane of shape ``(H, W)``.  Alpha
  is never part of the color array and is never color-transformed.
* ``premultiplied`` declares whether ``color`` is premultiplied by ``alpha``.
  The numeric kernel always converts *non-premultiplied* color; see
  ``kernel.py`` for the fixed unpremultiply / re-premultiply semantics.

Only 8-bit data is supported in this version of the contract.  CMYK is
additionally restricted (see ``profiles.py`` / ``service.py``): it requires an
explicit opt-in and a registry profile marked as CMYK-capable.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

from .errors import ContractError, FailureCategory


class ColorMode(enum.Enum):
    """Supported color modes.  Values match Pillow mode strings."""

    RGB = "RGB"
    GRAY = "L"
    LAB = "LAB"
    CMYK = "CMYK"

    @property
    def channels(self) -> int:
        return _CHANNELS[self]

    @property
    def channel_order(self) -> tuple[str, ...]:
        return CHANNEL_ORDER[self]


_CHANNELS = {
    ColorMode.RGB: 3,
    ColorMode.GRAY: 1,
    ColorMode.LAB: 3,
    ColorMode.CMYK: 4,
}

#: Fixed channel order per mode.  LAB uses the LittleCMS/Pillow 8-bit
#: storage convention: L* in [0, 255] maps to [0, 100]; a*/b* are signed
#: values in [-128, 127] stored wrapped into uint8 (i.e. byte = value mod
#: 256).  This matches ``Image.frombytes``/``tobytes`` for Pillow LAB images.
CHANNEL_ORDER = {
    ColorMode.RGB: ("R", "G", "B"),
    ColorMode.GRAY: ("L",),
    ColorMode.LAB: ("L*", "a*", "b*"),
    ColorMode.CMYK: ("C", "M", "Y", "K"),
}

#: Maps an ICC profile color-space signature to a contract color mode.
COLORSPACE_TO_MODE = {
    "RGB": ColorMode.RGB,
    "GRAY": ColorMode.GRAY,
    "Lab": ColorMode.LAB,
    "CMYK": ColorMode.CMYK,
}


@dataclass(frozen=True)
class ImageData:
    """An image under the contract.  Validate with :meth:`validate`."""

    color: np.ndarray
    mode: ColorMode
    alpha: np.ndarray | None = None
    premultiplied: bool = False

    @property
    def height(self) -> int:
        return int(self.color.shape[0])

    @property
    def width(self) -> int:
        return int(self.color.shape[1])

    @property
    def pixels(self) -> int:
        return self.height * self.width

    def validate(self) -> None:
        """Raise :class:`ContractError` listing every violation found."""
        problems: list[str] = []

        if not isinstance(self.color, np.ndarray):
            problems.append("color must be a numpy array")
        else:
            if self.color.dtype != np.uint8:
                problems.append(
                    f"color dtype must be uint8, got {self.color.dtype}"
                )
            if self.color.ndim != 3:
                problems.append(
                    f"color must have shape (H, W, C), got {self.color.shape}"
                )
            elif self.color.shape[2] != self.mode.channels:
                problems.append(
                    f"mode {self.mode.name} expects {self.mode.channels} "
                    f"channel(s) {self.mode.channel_order}, got "
                    f"{self.color.shape[2]}"
                )

        if self.alpha is not None:
            if not isinstance(self.alpha, np.ndarray):
                problems.append("alpha must be a numpy array")
            else:
                if self.alpha.dtype != np.uint8:
                    problems.append(
                        f"alpha dtype must be uint8, got {self.alpha.dtype}"
                    )
                expected = (
                    self.color.shape[:2]
                    if isinstance(self.color, np.ndarray) and self.color.ndim >= 2
                    else None
                )
                if self.alpha.ndim != 2 or (
                    expected is not None and self.alpha.shape != expected
                ):
                    problems.append(
                        f"alpha must have shape (H, W) matching color, got "
                        f"{self.alpha.shape}"
                    )
        elif self.premultiplied:
            problems.append("premultiplied=True requires an alpha plane")

        if problems:
            raise ContractError(
                FailureCategory.CONTRACT_VIOLATION,
                "image data contract violation: " + "; ".join(problems),
                detail={"problems": problems},
            )
