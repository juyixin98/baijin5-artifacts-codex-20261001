"""精度阶梯：每一级的分解精度、残差精度与迭代预算显式定义。

设计约定（对应"不同阶段精度明确"）：
- fp32 级：float32 LU 分解，float64 残差（原矩阵）。
- fp64 级：float64 LU 分解，longdouble（x86 80 位扩展）残差（原矩阵）。
- mpmath 级：任意精度 LU 分解与同精度残差（原矩阵的十进制原文）。

残差精度始终高于分解精度，这是迭代精化能够降低向后误差的前提。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from config.settings import SolverSettings


@dataclass(frozen=True)
class PrecisionTier:
    """一个精度阶段：分解精度、残差精度、机器 epsilon 与迭代预算。"""

    name: str
    factor_dtype: str  # float32 | float64 | mpmath
    residual_kind: str  # float64 | longdouble | mpmath
    eps: float  # 分解精度的机器 epsilon
    max_iter: int
    dps: int | None = None  # mpmath 阶段的十进制工作精度


def build_ladder(settings: SolverSettings) -> tuple[PrecisionTier, ...]:
    """按 fp32 -> fp64 -> mpmath 顺序构造升级阶梯。"""
    return (
        PrecisionTier(
            name="fp32",
            factor_dtype="float32",
            residual_kind="float64",
            eps=float(np.finfo(np.float32).eps),
            max_iter=settings.fp32_max_iter,
        ),
        PrecisionTier(
            name="fp64",
            factor_dtype="float64",
            residual_kind="longdouble",
            eps=float(np.finfo(np.float64).eps),
            max_iter=settings.fp64_max_iter,
        ),
        PrecisionTier(
            name="mpmath",
            factor_dtype="mpmath",
            residual_kind="mpmath",
            eps=10.0 ** (-settings.mp_dps),
            max_iter=settings.mp_max_iter,
            dps=settings.mp_dps,
        ),
    )
