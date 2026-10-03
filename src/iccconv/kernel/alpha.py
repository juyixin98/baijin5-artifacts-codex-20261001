"""Alpha channel handling.

The ICC engine transforms color channels only; alpha is always split off,
carried through untouched, and re-attached afterwards.

Premultiplication semantics are fixed:
* conversion always happens on *straight* (non-premultiplied) color;
* premultiplied input is unpremultiplied before conversion;
* premultiplied output is re-associated after conversion;
* pixels with alpha == 0 unpremultiply to color 0 (defined, not NaN).
"""

from __future__ import annotations

import numpy as np

from ..contract.enums import AlphaMode, ColorSpace


def split_alpha(
    pixels: np.ndarray, color_space: ColorSpace, alpha_mode: AlphaMode
) -> tuple[np.ndarray, np.ndarray | None]:
    """Return (color, alpha); alpha is None when alpha_mode is NONE."""
    base = color_space.base_channels
    if alpha_mode is AlphaMode.NONE:
        return pixels, None
    return pixels[..., :base], pixels[..., base]


def join_alpha(color: np.ndarray, alpha: np.ndarray | None) -> np.ndarray:
    if alpha is None:
        return color
    return np.concatenate([color, alpha[..., None]], axis=-1)


def unpremultiply(color: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Straight color from premultiplied color, uint8 in/out.

    out = round(color * 255 / alpha), clipped to [0, 255];
    alpha == 0 maps to color 0 by definition.
    """
    c = color.astype(np.float64)
    a = alpha.astype(np.float64)[..., None]
    out = np.zeros_like(c)
    np.divide(c * 255.0, a, out=out, where=a > 0)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def premultiply(color: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Premultiplied color from straight color, uint8 in/out."""
    c = color.astype(np.float64)
    a = alpha.astype(np.float64)[..., None]
    return np.clip(np.rint(c * a / 255.0), 0, 255).astype(np.uint8)
