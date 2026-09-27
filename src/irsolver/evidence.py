"""误差证据模块：向后误差、前向误差界与迭代轨迹记录。

判据定义（分量相对向后误差，Oettli–Prager / Rigal–Gaches 形式）：

    η(x) = max_i |b - A·x|_i / (|A|·|x| + |b|)_i

η ≤ tol 意味着 x 是某个系数相对扰动不超过 η 的邻近系统的精确解，
这是不依赖条件数的、可独立复核的接受判据。同时给出范数向后误差
‖r‖∞ / (‖A‖∞‖x‖∞ + ‖b‖∞) 与前向误差界 κ·η 作为佐证。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class IterationRecord:
    """单次迭代的证据记录（step_norm 在校正量求出后回填）。"""

    tier: str
    iteration: int
    eta_componentwise: float | None
    eta_normwise: float | None
    step_norm: float | None = None


def backward_errors(
    A64: np.ndarray, x64: np.ndarray, b64: np.ndarray, r64: np.ndarray
) -> tuple[float, float]:
    """返回 (分量向后误差, 范数向后误差)。零分母按定义处理。"""
    denom = np.abs(A64) @ np.abs(x64) + np.abs(b64)
    abs_r = np.abs(r64)
    ratios = []
    for ri, di in zip(abs_r, denom):
        if di > 0:
            ratios.append(float(ri / di))
        elif ri == 0:
            ratios.append(0.0)
        else:
            ratios.append(math.inf)
    eta_comp = max(ratios) if ratios else math.inf
    norm_r = float(np.linalg.norm(r64, np.inf))
    scale = float(
        np.linalg.norm(A64, np.inf) * np.linalg.norm(x64, np.inf)
        + np.linalg.norm(b64, np.inf)
    )
    if scale > 0:
        eta_norm = norm_r / scale
    else:
        eta_norm = 0.0 if norm_r == 0 else math.inf
    return eta_comp, eta_norm


def forward_error_bound(kappa: float, eta_componentwise: float) -> float:
    """前向相对误差的可用上界 κ·η（可能为 inf，如实报告）。"""
    bound = kappa * eta_componentwise
    return bound if math.isfinite(bound) else math.inf


def accuracy_note(kappa: float | None, method: str, tolerance: float) -> str:
    """可达精度限制说明：前向误差不可能优于 κ·ε（工作精度机器 epsilon）。"""
    if kappa is None:
        return "条件数未估计，无法给出可达精度说明。"
    if not math.isfinite(kappa):
        return "条件数趋于无穷（矩阵奇异或数值奇异），前向误差无任何保证。"
    f32 = kappa * 2.0**-24
    f64 = kappa * 2.0**-53
    bound = kappa * tolerance
    return (
        f"条件数 κ≈{kappa:.3e}（{method}）。前向相对误差不可能优于 κ·ε："
        f"fp32 工作精度下约 {f32:.1e}，fp64 下约 {f64:.1e}。"
        f"收敛判据 η≤{tolerance:.1e} 对应前向相对误差界 κ·η≈{bound:.1e}；"
        f"当 κ·η≥1 时解的每一位都可能不可信，应以后向误差 η 为准。"
    )
