"""残差计算内核：r = b - A·x。

关键不变量（对应"残差必须使用原矩阵计算"）：
残差始终由原始输入矩阵计算 —— fp32/fp64 级使用原矩阵的 float64/longdouble
映像，mpmath 级使用输入的十进制原文，绝不使用低精度分解中被舍入的矩阵。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mpmath as mp
import numpy as np

from .inputs import MatrixInput


@dataclass
class Residual:
    """一次残差计算的结果。"""

    native: object  # 当前阶段精度下的残差（作为校正方程右端）
    as_float64: np.ndarray  # 报表与判据用的 float64 映像


class PreparedSystem:
    """按精度级别惰性准备原矩阵与各右端列的操作数，避免重复转换。"""

    def __init__(self, A: MatrixInput, B: MatrixInput, mp_dps: int):
        self.A = A
        self.B = B
        self.mp_dps = mp_dps
        self._cache: dict[str, tuple[object, list[object]]] = {}

    @property
    def A64(self) -> np.ndarray:
        return self.operands("float64")[0]  # type: ignore[return-value]

    def operands(self, kind: str) -> tuple[object, list[object]]:
        """返回 (原矩阵操作数, 各右端列操作数列表)。"""
        if kind not in self._cache:
            self._cache[kind] = self._build(kind)
        return self._cache[kind]

    def _build(self, kind: str) -> tuple[object, list[object]]:
        if kind == "float64":
            A64 = self.A.to_float64()
            B64 = self.B.to_float64()
            return A64, [B64[:, j].copy() for j in range(B64.shape[1])]
        if kind == "longdouble":
            Ald = self.A.to_float64().astype(np.longdouble)
            Bld = self.B.to_float64().astype(np.longdouble)
            return Ald, [Bld[:, j].copy() for j in range(Bld.shape[1])]
        if kind == "mpmath":
            with mp.workdps(self.mp_dps):
                Amp = self.A.to_mpmath()
                Bmp = self.B.to_mpmath()
                cols = [
                    mp.matrix([Bmp[i, j] for i in range(Bmp.rows)])
                    for j in range(Bmp.cols)
                ]
            return Amp, cols
        raise ValueError(f"未知残差精度类别: {kind}")


def _mpf_to_float(v: mp.mpf) -> float:
    try:
        return float(v)
    except OverflowError:
        return math.inf if v > 0 else -math.inf


def compute_residual(
    prep: PreparedSystem, col: int, x_native: object, kind: str
) -> Residual:
    """用原矩阵按指定精度计算第 col 列的残差 r = b - A·x。"""
    A_op, b_cols = prep.operands(kind)
    b = b_cols[col]
    if kind == "mpmath":
        with mp.workdps(prep.mp_dps):
            x_mp = mp.matrix(list(x_native))  # type: ignore[arg-type]
            r = b - A_op * x_mp  # type: ignore[operator]
            native = [r[i] for i in range(r.rows)]
            return Residual(
                native=native,
                as_float64=np.array([_mpf_to_float(v) for v in native]),
            )
    x_arr = np.asarray(x_native, dtype=np.asarray(b).dtype)
    r = b - A_op @ x_arr  # type: ignore[operator]
    return Residual(native=r, as_float64=np.asarray(r, dtype=np.float64))
