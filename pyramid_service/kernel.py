"""数值内核：层尺寸序列、像素中心坐标映射、面积加权 2 倍降采样。

降采样采用明确的面积（box）抗混叠：输出像素 (i, j) 覆盖输入行
[2i, 2i+2) ∩ [0, H)、列 [2j, 2j+2) ∩ [0, W)，取覆盖像素的算术平均。
奇数尺寸边缘的输出像素只平均实际覆盖的 1 行或 1 列，不丢行列。
"""
from __future__ import annotations

import numpy as np


def level_shape(height: int, width: int, level: int) -> tuple[int, int]:
    """层级 L 的 (height, width)：ceil(size_0 / 2**L)。"""
    if level < 0:
        raise ValueError(f"level must be >= 0, got {level}")
    if height < 1 or width < 1:
        raise ValueError(f"shape must be positive, got {height}x{width}")
    s = 1 << level
    return ((height + s - 1) // s, (width + s - 1) // s)


def level_shapes(height: int, width: int, n_levels: int) -> list[tuple[int, int]]:
    return [level_shape(height, width, lv) for lv in range(n_levels)]


def max_levels_for(height: int, width: int) -> int:
    """降到 1x1 为止的层数（含第 0 层）。"""
    n = 1
    h, w = height, width
    while h > 1 or w > 1:
        h = (h + 1) // 2
        w = (w + 1) // 2
        n += 1
    return n


def pixel_center_to_source(row: float, col: float, level: int) -> tuple[float, float]:
    """层级 L 像素中心 -> 原图坐标（固定映射，不随内容变化）。"""
    scale = float(1 << level)
    return ((row + 0.5) * scale - 0.5, (col + 0.5) * scale - 0.5)


def source_to_pixel_center(src_row: float, src_col: float, level: int) -> tuple[float, float]:
    """原图坐标 -> 层级 L 像素中心坐标（pixel_center_to_source 的逆）。"""
    scale = float(1 << level)
    return ((src_row + 0.5) / scale - 0.5, (src_col + 0.5) / scale - 0.5)


def pixel_support_source(row: int, col: int, level: int, src_height: int, src_width: int):
    """输出像素在原图中的支持区间（裁剪到原图边界）。

    返回 ((r0, r1), (c0, c1))，半开区间。每个输出像素至少覆盖一个
    源像素（奇数边界不丢行列的保证由此可检验）。
    """
    s = 1 << level
    r0, c0 = row * s, col * s
    r1, c1 = min(r0 + s, src_height), min(c0 + s, src_width)
    if r0 >= r1 or c0 >= c1:
        raise ValueError(
            f"pixel ({row}, {col}) at level {level} has empty support in {src_height}x{src_width}"
        )
    return (r0, r1), (c0, c1)


def area_downsample2(image: np.ndarray) -> np.ndarray:
    """面积加权 2 倍降采样（明确抗混叠 box 滤波）。

    输入 (H, W) 任意 dtype，输出 float32，形状 (ceil(H/2), ceil(W/2))。
    内部以 float64 累加，边缘块按实际覆盖像素数归一化。
    """
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim != 2:
        raise ValueError(f"expected 2-D single-channel array, got ndim={arr.ndim}")
    h, w = arr.shape
    if h < 1 or w < 1:
        raise ValueError(f"image must be non-empty, got {h}x{w}")
    ho, wo = (h + 1) // 2, (w + 1) // 2
    acc = np.zeros((ho, wo), dtype=np.float64)
    cnt = np.zeros((ho, wo), dtype=np.float64)
    for di in (0, 1):
        for dj in (0, 1):
            sub = arr[di::2, dj::2]
            rows, cols = sub.shape
            acc[:rows, :cols] += sub
            cnt[:rows, :cols] += 1.0
    return (acc / cnt).astype(np.float32)
