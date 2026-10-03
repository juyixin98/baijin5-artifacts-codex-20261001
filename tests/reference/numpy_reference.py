"""Independent reference implementation: sRGB -> Adobe RGB (1998).

Computes the conversion from first principles using only NumPy/SciPy:
ICC-published primaries, the sRGB piecewise TRC, the Adobe RGB gamma
(563/256 = 2.19921875) and Bradford chromatic adaptation D65 -> D50.

This module deliberately does NOT import littleCMS/Pillow ImageCms, so
the kernel tests compare the engine against an independent code path.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import inv

D65 = np.array([0.95047, 1.0, 1.08883])
D50 = np.array([0.96422, 1.0, 0.82521])

BRADFORD = np.array(
    [
        [0.8951, 0.2664, -0.1614],
        [-0.7502, 1.7135, 0.0367],
        [0.0389, -0.0685, 1.0296],
    ]
)

# Linear RGB -> XYZ (D65) matrices from the respective ICC specifications.
M_SRGB_D65 = np.array(
    [
        [0.4123908, 0.3575843, 0.1804808],
        [0.2126390, 0.7151687, 0.0721923],
        [0.0193308, 0.1191948, 0.9505322],
    ]
)
M_ADOBE_RGB_D65 = np.array(
    [
        [0.57667, 0.18556, 0.18823],
        [0.29734, 0.62736, 0.07529],
        [0.02703, 0.07069, 0.99134],
    ]
)

ADOBE_GAMMA = 563.0 / 256.0  # 2.19921875


def bradford_adaptation(src_white: np.ndarray, dst_white: np.ndarray) -> np.ndarray:
    """3x3 matrix adapting XYZ from src_white to dst_white (Bradford CAT)."""
    src_cone = BRADFORD @ src_white
    dst_cone = BRADFORD @ dst_white
    return inv(BRADFORD) @ np.diag(dst_cone / src_cone) @ BRADFORD


def srgb_decode(c: np.ndarray) -> np.ndarray:
    """sRGB electro-optical transfer function, c in [0, 1] -> linear."""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def adobe_encode(linear: np.ndarray) -> np.ndarray:
    """Adobe RGB (1998) opto-electronic transfer function."""
    return np.power(np.clip(linear, 0.0, None), 1.0 / ADOBE_GAMMA)


def srgb_to_adobergb_reference(rgb_u8: np.ndarray) -> np.ndarray:
    """Reference sRGB(D65) -> AdobeRGB(profile PCS is D50), uint8 in/out.

    Mirrors what a relative-colorimetric matrix-shaper conversion does:
    decode TRC, to XYZ D65, adapt to D50, invert the D50-adapted Adobe
    matrix, encode the Adobe gamma.
    """
    rgb = rgb_u8.astype(np.float64) / 255.0
    linear = srgb_decode(rgb)
    xyz65 = linear @ M_SRGB_D65.T
    adapt = bradford_adaptation(D65, D50)
    xyz50 = xyz65 @ adapt.T
    m_adobe_d50 = adapt @ M_ADOBE_RGB_D65
    adobe_linear = xyz50 @ inv(m_adobe_d50).T
    encoded = adobe_encode(np.clip(adobe_linear, 0.0, 1.0))
    return np.rint(encoded * 255.0).astype(np.uint8)
