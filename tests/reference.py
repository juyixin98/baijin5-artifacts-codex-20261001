"""测试共享设施。

reference.py 中的参考实现与被测内核相互独立：
- ref_downsample2 用显式双重循环逐块求均值（被测内核用步长切片累加）；
- 小尺寸用例另附手工计算的硬编码期望值。
"""
from __future__ import annotations

import numpy as np


def ref_downsample2(img: np.ndarray) -> np.ndarray:
    """独立参考：显式逐输出像素、逐源像素循环的面积平均。"""
    img = np.asarray(img, dtype=np.float64)
    h, w = img.shape
    ho, wo = (h + 1) // 2, (w + 1) // 2
    out = np.empty((ho, wo), dtype=np.float64)
    for i in range(ho):
        for j in range(wo):
            total = 0.0
            count = 0
            for r in range(2 * i, min(2 * i + 2, h)):
                for c in range(2 * j, min(2 * j + 2, w)):
                    total += img[r, c]
                    count += 1
            out[i, j] = total / count
    return out


def ref_pyramid(img: np.ndarray, n_levels: int) -> list[np.ndarray]:
    levels = [np.asarray(img, dtype=np.float64)]
    for _ in range(n_levels - 1):
        levels.append(ref_downsample2(levels[-1]))
    return levels
