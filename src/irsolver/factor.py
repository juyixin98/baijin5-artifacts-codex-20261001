"""分解内核：各精度等级的 LU 分解与校正方程求解。

fp32/fp64 使用 SciPy 部分主元 LU（严格保持指定 dtype）；
mpmath 级使用本模块实现的部分主元 LU（任意精度，遇精确零主元抛
SingularFactorError，作为奇异判定的证据之一）。
"""
from __future__ import annotations

import mpmath as mp
import numpy as np
import scipy.linalg as sla

from .inputs import MatrixInput
from .precision import PrecisionTier


class SingularFactorError(Exception):
    """分解中遇到精确零主元（矩阵奇异）。"""


class ScipyLUFactor:
    """float32/float64 LU 分解，求解时把右端转换到分解精度。"""

    def __init__(self, A64: np.ndarray, dtype: np.dtype):
        self.dtype = np.dtype(dtype)
        self._lu, self._piv = sla.lu_factor(A64.astype(self.dtype))

    def solve(self, r_native: object) -> np.ndarray:
        rhs = np.asarray(r_native, dtype=self.dtype)
        return sla.lu_solve((self._lu, self._piv), rhs)


def mp_lu_factor(
    A_rows: list[list[mp.mpf]],
) -> tuple[list[list[mp.mpf]], list[int]]:
    """部分主元 LU（任意精度），返回紧凑 LU 与行置换。"""
    n = len(A_rows)
    LU = [row[:] for row in A_rows]
    perm = list(range(n))
    for k in range(n):
        p = max(range(k, n), key=lambda i: abs(LU[i][k]))
        if LU[p][k] == 0:
            raise SingularFactorError(f"第 {k} 步主元精确为零")
        if p != k:
            LU[k], LU[p] = LU[p], LU[k]
            perm[k], perm[p] = perm[p], perm[k]
        for i in range(k + 1, n):
            LU[i][k] /= LU[k][k]
            lik = LU[i][k]
            for j in range(k + 1, n):
                LU[i][j] -= lik * LU[k][j]
    return LU, perm


def mp_lu_solve(
    LU: list[list[mp.mpf]], perm: list[int], b: list[mp.mpf]
) -> list[mp.mpf]:
    """前代/回代求解 LU·x = P·b。"""
    n = len(b)
    y = [b[perm[i]] for i in range(n)]
    for i in range(n):
        for j in range(i):
            y[i] -= LU[i][j] * y[j]
    x = y[:]
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n):
            x[i] -= LU[i][j] * x[j]
        x[i] /= LU[i][i]
    return x


class MpLUFactor:
    """mpmath 任意精度 LU 分解（基于原矩阵的十进制原文）。"""

    def __init__(self, A: MatrixInput, dps: int):
        self.dps = dps
        with mp.workdps(dps):
            rows = [[mp.mpf(v) for v in row] for row in A.text]
            self._lu, self._perm = mp_lu_factor(rows)

    def solve(self, r_native: object) -> list[mp.mpf]:
        with mp.workdps(self.dps):
            return mp_lu_solve(self._lu, self._perm, list(r_native))  # type: ignore[arg-type]


def build_factor(
    tier: PrecisionTier, A: MatrixInput, A64: np.ndarray
) -> ScipyLUFactor | MpLUFactor:
    """按阶段构造分解对象（可能抛 SingularFactorError）。"""
    if tier.factor_dtype == "mpmath":
        return MpLUFactor(A, tier.dps or 60)
    return ScipyLUFactor(A64, np.dtype(tier.factor_dtype))
