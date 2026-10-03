"""Synthetic source images used by the service and the test-suite.

All generators return the canonical pixel contract: ``float64`` arrays of
shape ``(H, W, C)`` with values in ``[0.0, 1.0]``.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .errors import InputValidationError

PATTERN_NAMES = ("checkerboard", "diagonal", "gradient", "noise")


def _broadcast(plane: np.ndarray, channels: int) -> np.ndarray:
    if channels not in (1, 3):
        raise InputValidationError(f"channels must be 1 or 3, got {channels}")
    out = np.repeat(plane[..., None], channels, axis=2)
    return out.astype(np.float64, copy=False)


def checkerboard(h: int, w: int, channels: int = 1, period: int = 1) -> np.ndarray:
    """Alternating 0/1 squares of ``period`` pixels."""
    if period < 1:
        raise InputValidationError(f"period must be >= 1, got {period}")
    yy, xx = np.indices((h, w))
    plane = ((yy // period + xx // period) % 2).astype(np.float64)
    return _broadcast(plane, channels)


def diagonal_lines(
    h: int, w: int, channels: int = 1, spacing: int = 8, thickness: int = 1
) -> np.ndarray:
    """White diagonal stripes on black — stresses anti-aliasing."""
    if spacing < 1 or thickness < 1:
        raise InputValidationError("spacing and thickness must be >= 1")
    yy, xx = np.indices((h, w))
    plane = ((xx + yy) % spacing < thickness).astype(np.float64)
    return _broadcast(plane, channels)


def gradient(h: int, w: int, channels: int = 1) -> np.ndarray:
    """Smooth bilinear ramp in [0, 1] — downsampling must reproduce it."""
    yy, xx = np.indices((h, w), dtype=np.float64)
    plane = 0.5 * (xx / max(w - 1, 1) + yy / max(h - 1, 1))
    return _broadcast(plane, channels)


def noise(h: int, w: int, channels: int = 1, seed: Optional[int] = 0) -> np.ndarray:
    """Deterministic uniform noise (seeded) — used for reference comparisons."""
    rng = np.random.default_rng(0 if seed is None else seed)
    return rng.random((h, w, channels), dtype=np.float64)


def make_pattern(
    name: str, h: int, w: int, channels: int = 1, seed: Optional[int] = 0
) -> np.ndarray:
    if h <= 0 or w <= 0:
        raise InputValidationError(f"pattern size must be positive, got {w}x{h}")
    if name == "checkerboard":
        return checkerboard(h, w, channels)
    if name == "diagonal":
        return diagonal_lines(h, w, channels)
    if name == "gradient":
        return gradient(h, w, channels)
    if name == "noise":
        return noise(h, w, channels, seed)
    raise InputValidationError(
        f"unknown pattern {name!r}; expected one of {PATTERN_NAMES}"
    )
