"""条件数估计与数值秩判定。

估计策略：先用 float64 SVD 快速估计；当结果超过阈值（默认 1e12）或不可用
（float64 已无法分辨最小奇异值）时，升级到 mpmath 高精度 SVD 复核，并在
同一高精度下做数值秩判定 —— 秩判定精度不低于 50 位十进制，阈值取
σmax·n·10^-(dps-10)，使精确奇异矩阵与仅病态矩阵可被明确区分。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mpmath as mp
import numpy as np

from .inputs import MatrixInput

_RANK_CHECK_MIN_DPS = 50  # 秩判定的最低高精度工作位数
_RANK_GUARD_DIGITS = 10  # 秩阈值保留的保护位数


@dataclass(frozen=True)
class ConditionReport:
    """条件数估计结果。rank/rank_deficient 仅在高精度复核后填写。"""

    kappa: float | None
    method: str  # "svd-float64" | "svd-mpmath(dps=N)"
    rank: int | None
    rank_deficient: bool | None


def _cond_fp64(A64: np.ndarray) -> ConditionReport:
    sigma = np.linalg.svd(A64, compute_uv=False)
    smax = float(sigma[0])
    smin = float(sigma[-1])
    kappa = smax / smin if smin > 0 else math.inf
    return ConditionReport(kappa, "svd-float64", None, None)


def _cond_mp(A: MatrixInput, dps: int) -> ConditionReport:
    n = A.n_rows
    with mp.workdps(dps):
        _, S, _ = mp.svd(A.to_mpmath())
        smax = max(S)
        smin = min(S)
        tol = smax * n * mp.mpf(10) ** (-(dps - _RANK_GUARD_DIGITS))
        rank = sum(1 for s in S if s > tol)
        kappa = smax / smin if smin > 0 else mp.inf
    return ConditionReport(
        float(kappa) if mp.isfinite(kappa) else math.inf,
        f"svd-mpmath(dps={dps})",
        rank,
        rank < n,
    )


def estimate_condition(
    A: MatrixInput, A64: np.ndarray, threshold: float, mp_dps: int
) -> ConditionReport:
    """估计条件数；病态严重时自动升级高精度 SVD 并给出秩判定。"""
    base = _cond_fp64(A64)
    if math.isfinite(base.kappa) and base.kappa < threshold:
        return base
    return _cond_mp(A, max(mp_dps, _RANK_CHECK_MIN_DPS))
