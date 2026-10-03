"""Independent analytic color-science reference used ONLY by tests.

Implemented from published specifications, not from the code under test:

* sRGB electro-optical transfer function: IEC 61966-2-1
* sRGB -> CIE XYZ matrix: IEC 61966-2-1 (D65)
* Chromatic adaptation D65 -> D50: Bradford matrices (ICC PCS is D50)
* CIE L*a*b*: CIE 15:2004

Nothing in ``colorconvert/`` imports this module; golden vectors in
``fixtures/golden_vectors.json`` are generated from it by
``tools/make_fixtures.py``.
"""
from __future__ import annotations

import numpy as np

# IEC 61966-2-1 linear-RGB -> XYZ (D65) matrix.
M_SRGB_TO_XYZ = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)

WHITE_D65 = np.array([0.95047, 1.00000, 1.08883])
WHITE_D50 = np.array([0.96422, 1.00000, 0.82521])

# Bradford chromatic adaptation matrices.
M_BRADFORD = np.array(
    [
        [0.8951, 0.2664, -0.1614],
        [-0.7502, 1.7135, 0.0367],
        [0.0389, -0.0685, 1.0296],
    ]
)
M_BRADFORD_INV = np.linalg.inv(M_BRADFORD)

_DELTA = 6.0 / 29.0


def srgb8_to_linear(c: np.ndarray) -> np.ndarray:
    """IEC 61966-2-1 EOTF.  Input uint8-ish [0, 255], output [0, 1]."""
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_srgb_to_xyz(rgb: np.ndarray) -> np.ndarray:
    return np.asarray(rgb, dtype=np.float64) @ M_SRGB_TO_XYZ.T


def adapt_xyz_d65_to_d50(xyz: np.ndarray) -> np.ndarray:
    """Bradford adaptation from D65 to the ICC PCS illuminant D50."""
    xyz = np.asarray(xyz, dtype=np.float64)
    src_lms = WHITE_D65 @ M_BRADFORD.T
    dst_lms = WHITE_D50 @ M_BRADFORD.T
    scale = np.diag(dst_lms / src_lms)
    m = M_BRADFORD_INV @ scale @ M_BRADFORD
    return xyz @ m.T


def xyz_to_lab(xyz: np.ndarray, white: np.ndarray = WHITE_D50) -> np.ndarray:
    """CIE L*a*b* (CIE 15:2004), default white = D50 (ICC PCS)."""
    xyz = np.asarray(xyz, dtype=np.float64)
    t = xyz / np.asarray(white, dtype=np.float64)
    f = np.where(t > _DELTA**3, np.cbrt(t), t / (3 * _DELTA**2) + 4.0 / 29.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def srgb8_to_lab_d50(rgb8) -> np.ndarray:
    """Full chain: 8-bit sRGB -> CIE L*a*b* relative to D50."""
    linear = srgb8_to_linear(np.asarray(rgb8, dtype=np.float64))
    xyz = linear_srgb_to_xyz(linear)
    xyz50 = adapt_xyz_d65_to_d50(xyz)
    return xyz_to_lab(xyz50, WHITE_D50)


def lab_to_pillow8(lab: np.ndarray) -> np.ndarray:
    """Encode Lab the way LittleCMS/Pillow store 8-bit LAB images.

    L*: [0, 100] -> [0, 255]; a*/b*: signed values in [-128, 127] stored
    wrapped into uint8 (byte = round(value) mod 256).
    """
    lab = np.asarray(lab, dtype=np.float64)
    L8 = np.clip(np.rint(lab[..., 0] * 255.0 / 100.0), 0, 255)
    a8 = np.mod(np.rint(np.clip(lab[..., 1], -128, 127)), 256)
    b8 = np.mod(np.rint(np.clip(lab[..., 2], -128, 127)), 256)
    return np.stack([L8, a8, b8], axis=-1).astype(np.uint8)
