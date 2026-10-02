"""合成输入生成器。

所有生成器都是纯坐标函数：generate_window(spec, y0, x0, h, w) 返回
原图窗口 [y0:y0+h, x0:x0+w) 的像素值。任意窗口的结果与整图生成后切片
完全一致，因此分块作业与整图参考可以逐像素对比。
"""
from __future__ import annotations

import numpy as np

from .contracts import GeneratorSpec
from .errors import InputError

def _window_grid(y0: int, x0: int, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[y0 : y0 + h, x0 : x0 + w]
    return yy.astype(np.int64), xx.astype(np.int64)


def _checkerboard(yy: np.ndarray, xx: np.ndarray, params: dict) -> np.ndarray:
    period = int(params.get("period", 8))
    if period < 1:
        raise InputError(f"checkerboard period must be >= 1, got {period}")
    high = float(params.get("high", 255.0))
    low = float(params.get("low", 0.0))
    parity = (yy // period + xx // period) % 2
    return np.where(parity == 0, high, low)


def _diagonal(yy: np.ndarray, xx: np.ndarray, params: dict) -> np.ndarray:
    period = int(params.get("period", 16))
    if period < 1:
        raise InputError(f"diagonal period must be >= 1, got {period}")
    width = int(params.get("width", 1))
    if width < 1 or width > period:
        raise InputError(f"diagonal width must be in [1, {period}], got {width}")
    high = float(params.get("high", 255.0))
    on_line = (xx - yy) % period < width
    return np.where(on_line, high, 0.0)


def _gradient(yy: np.ndarray, xx: np.ndarray, params: dict) -> np.ndarray:
    kx = int(params.get("kx", 3))
    ky = int(params.get("ky", 5))
    return (xx * kx + yy * ky) % 256


def _noise(yy: np.ndarray, xx: np.ndarray, params: dict) -> np.ndarray:
    """坐标哈希噪声：同一坐标恒定同值，与窗口原点无关。"""
    seed = int(params.get("seed", 0)) & 0xFFFFFFFF
    # uint64 算术自然按 2^64 回绕，无需显式取模
    y = yy.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15)
    x = xx.astype(np.uint64) * np.uint64(0xC2B2AE3D27D4EB4F)
    z = y ^ x ^ np.uint64(seed)
    z = (z ^ (z >> np.uint64(29))) * np.uint64(0xBF58476D1CE4E5B9)
    z = z ^ (z >> np.uint64(32))
    return (z % np.uint64(256)).astype(np.float64)


_GENERATORS = {
    "checkerboard": _checkerboard,
    "diagonal": _diagonal,
    "gradient": _gradient,
    "noise": _noise,
}


def generate_window(spec: GeneratorSpec, y0: int, x0: int, h: int, w: int) -> np.ndarray:
    """生成原图窗口 [y0:y0+h, x0:x0+w)，返回 float32 (h, w)。"""
    if h < 1 or w < 1:
        raise InputError(f"window must be non-empty, got {h}x{w}")
    if y0 < 0 or x0 < 0 or y0 + h > spec.height or x0 + w > spec.width:
        raise InputError(
            f"window (y0={y0}, x0={x0}, h={h}, w={w}) outside image "
            f"{spec.width}x{spec.height}"
        )
    fn = _GENERATORS.get(spec.kind)
    if fn is None:
        raise InputError(f"unknown generator {spec.kind!r}")
    yy, xx = _window_grid(y0, x0, h, w)
    values = fn(yy, xx, dict(spec.params))
    return np.asarray(values, dtype=np.float32)
