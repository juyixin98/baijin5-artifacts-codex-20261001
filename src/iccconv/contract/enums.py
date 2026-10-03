"""Enumerations of the image data contract."""

from __future__ import annotations

from enum import Enum, IntEnum


class ColorSpace(str, Enum):
    RGB = "RGB"
    GRAY = "GRAY"
    CMYK = "CMYK"

    @property
    def base_channels(self) -> int:
        return {"RGB": 3, "GRAY": 1, "CMYK": 4}[self.value]


class AlphaMode(str, Enum):
    """How the alpha channel relates to the color channels.

    NONE           - no alpha channel present.
    STRAIGHT       - color channels hold unassociated (non-premultiplied) color.
    PREMULTIPLIED  - color channels are multiplied by alpha (associated).
    """

    NONE = "none"
    STRAIGHT = "straight"
    PREMULTIPLIED = "premultiplied"


class RenderingIntent(IntEnum):
    """ICC rendering intents, values match the ICC / littleCMS numbering."""

    PERCEPTUAL = 0
    RELATIVE_COLORIMETRIC = 1
    SATURATION = 2
    ABSOLUTE_COLORIMETRIC = 3
