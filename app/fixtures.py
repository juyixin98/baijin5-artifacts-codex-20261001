"""Synthetic fixtures: deterministic images and kernels, plus PNG I/O.

Everything is generated locally from explicit seeds — no external accounts,
no real business data.  Pillow is used for the PNG round-trip part of the
image data contract.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .contract import KernelSpec, SeparableKernelSpec


# ---------------------------------------------------------------- images
def impulse_image(shape: tuple[int, int], pos: tuple[int, int] | None = None,
                  value: float = 1.0) -> np.ndarray:
    """Single non-zero pixel; default position is the image centre."""
    img = np.zeros(shape, dtype=np.float64)
    y, x = pos if pos is not None else (shape[0] // 2, shape[1] // 2)
    img[y, x] = value
    return img


def step_edge_image(shape: tuple[int, int], axis: int = 1,
                    loc: int | None = None, low: float = 0.0,
                    high: float = 1.0) -> np.ndarray:
    """Sharp step edge along ``axis`` (0 -> horizontal edge, 1 -> vertical)."""
    img = np.full(shape, low, dtype=np.float64)
    if axis == 1:
        cut = loc if loc is not None else shape[1] // 2
        img[:, cut:] = high
    else:
        cut = loc if loc is not None else shape[0] // 2
        img[cut:, :] = high
    return img


def tagged_border_image(shape: tuple[int, int], interior: float = 0.0,
                        border_values: tuple[float, float, float, float] = (1.0, 2.0, 3.0, 4.0),
                        border: int = 1) -> np.ndarray:
    """Interior ``interior``; each border band carries a distinct tag value.

    Order: top, bottom, left, right (corners take the last writer, right).
    Lets boundary tests tell exactly which edge a mirrored/wrapped sample
    came from.
    """
    top, bottom, left, right = border_values
    img = np.full(shape, interior, dtype=np.float64)
    img[:border, :] = top
    img[-border:, :] = bottom
    img[:, :border] = left
    img[:, -border:] = right
    return img


def seeded_noise_image(shape: tuple[int, int], seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(shape).astype(np.float64)


# ---------------------------------------------------------------- kernels
def box_kernel(k: int) -> KernelSpec:
    return KernelSpec.from_array(np.ones((k, k)) / (k * k))


def gaussian_kernel(k: int, sigma: float) -> KernelSpec:
    half = np.arange(k) - k // 2
    g = np.exp(-0.5 * (half / sigma) ** 2)
    full = np.outer(g, g)
    return KernelSpec.from_array(full / full.sum())


def separable_gaussian(k: int, sigma: float) -> SeparableKernelSpec:
    half = np.arange(k) - k // 2
    g = np.exp(-0.5 * (half / sigma) ** 2)
    g = g / g.sum()
    return SeparableKernelSpec.from_vectors(g, g)


def even_kernel(k: int, seed: int = 0) -> KernelSpec:
    """Random even-sized kernel; anchor defaults to k//2 (offsets -k/2..k/2-1)."""
    if k % 2 != 0:
        raise ValueError("k must be even")
    rng = np.random.default_rng(seed)
    return KernelSpec.from_array(rng.standard_normal((k, k)))


def random_kernel(shape: tuple[int, int], seed: int,
                  anchor: tuple[int, int] | None = None) -> KernelSpec:
    rng = np.random.default_rng(seed)
    return KernelSpec.from_array(rng.standard_normal(shape), anchor=anchor)


# ---------------------------------------------------------------- PNG I/O
def save_png(path: str | Path, img: np.ndarray) -> None:
    """Persist a float image as 8-bit PNG (values clipped to [0, 1])."""
    arr = np.clip(img, 0.0, 1.0)
    Image.fromarray((arr * 255.0).round().astype(np.uint8), mode="L").save(path)


def load_png(path: str | Path) -> np.ndarray:
    """Load a grayscale PNG as float64 in [0, 1]."""
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.float64) / 255.0
