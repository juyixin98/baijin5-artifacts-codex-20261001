"""Deterministic synthetic fixtures: ring, thin bridge, fork, cross-tile stroke.

These builders are the single source of truth for the sample PNGs in
``data/`` (written by ``scripts/make_samples.py``) and for the API's
``sample`` request option. Tests assert hand-computed expectations
against them; no expected value is produced by the thinning kernel.
"""

from __future__ import annotations

import numpy as np


def ring(size: int = 31, outer_radius: float = 12.0, inner_radius: float = 6.0) -> np.ndarray:
    """Filled annulus: 1 component, 1 hole, 0 endpoints."""
    yy, xx = np.mgrid[0:size, 0:size]
    center = (size - 1) / 2.0
    dist = np.hypot(yy - center, xx - center)
    return ((dist <= outer_radius) & (dist >= inner_radius)).astype(np.uint8)


def thin_bridge() -> np.ndarray:
    """Two 7x7 blocks joined by a 1-pixel bridge: 1 component, 0 holes."""
    img = np.zeros((12, 25), dtype=np.uint8)
    img[2:9, 2:9] = 1
    img[2:9, 16:23] = 1
    img[5, 9:16] = 1
    return img


def fork() -> np.ndarray:
    """Y-shaped 1-pixel stroke: 3 endpoints, 1 junction after thinning."""
    img = np.zeros((20, 21), dtype=np.uint8)
    img[2:11, 10] = 1  # stem
    for i in range(7):  # two diagonal arms
        img[10 + i, 10 - i] = 1
        img[10 + i, 10 + i] = 1
    return img


def cross_tile_stroke(size: int = 24, thickness: int = 3) -> np.ndarray:
    """Thick diagonal band crossing tile boundaries (tile_size=8 -> 3x3 grid)."""
    yy, xx = np.mgrid[0:size, 0:size]
    band = np.abs(yy - xx) <= (thickness - 1)
    return band.astype(np.uint8)


def plus_3x3() -> np.ndarray:
    """Compact plus sign: arm tips have B=3, so it thins to the centre pixel."""
    img = np.zeros((3, 3), dtype=np.uint8)
    img[1, :] = 1
    img[:, 1] = 1
    return img


def plus_7x7() -> np.ndarray:
    """Wide plus: inner 5 pixels cluster to 1 junction; 4 endpoint arms."""
    img = np.zeros((7, 7), dtype=np.uint8)
    img[1:6, 3] = 1
    img[3, 1:6] = 1
    return img


def block_3x3() -> np.ndarray:
    """Solid 3x3 block: thins to exactly the centre pixel."""
    return np.ones((3, 3), dtype=np.uint8)


def line_1x5() -> np.ndarray:
    """Horizontal 1-pixel line: a fixed point of the kernel."""
    img = np.zeros((1, 5), dtype=np.uint8)
    img[0, :] = 1
    return img


SAMPLES = {
    "ring": ring,
    "bridge": thin_bridge,
    "fork": fork,
    "cross_tile_stroke": cross_tile_stroke,
    "plus": plus_3x3,
    "plus_wide": plus_7x7,
    "block": block_3x3,
    "line": line_1x5,
}


def load_sample(name: str) -> np.ndarray:
    if name not in SAMPLES:
        raise KeyError(f"unknown sample {name!r}; available: {sorted(SAMPLES)}")
    return SAMPLES[name]()
