"""独立参考实现：Householder QR 任意精度求解 + 独立向后误差复核。

本模块与核心求解内核（refinement/factor/residual）不共享任何代码路径，
仅供测试与验收脚本生成对照答案，满足"参考答案不能全部由被测核心实现
自身生成"的要求。核心求解流程不依赖本模块。
"""
from __future__ import annotations

import mpmath as mp

from .inputs import MatrixInput


class ReferenceError(Exception):
    """参考求解失败（例如矩阵奇异导致上三角对角元过小）。"""


def _householder_solve(
    A_text: tuple[tuple[str, ...], ...], b_text: list[str], dps: int
) -> list[mp.mpf]:
    """Householder QR 求解（无列主元，任意精度），与核心 LU 路径完全独立。"""
    n = len(A_text)
    M = [[mp.mpf(v) for v in row] for row in A_text]
    y = [mp.mpf(v) for v in b_text]
    for k in range(n - 1):
        sigma = mp.sqrt(sum(M[i][k] ** 2 for i in range(k, n)))
        if sigma == 0:
            continue
        v = [M[i][k] for i in range(k, n)]
        v[0] += sigma if M[k][k] >= 0 else -sigma
        beta = 2 / sum(vi * vi for vi in v)
        for j in range(k, n):
            s = sum(v[i - k] * M[i][j] for i in range(k, n))
            for i in range(k, n):
                M[i][j] -= beta * v[i - k] * s
        s = sum(v[i - k] * y[i] for i in range(k, n))
        for i in range(k, n):
            y[i] -= beta * v[i - k] * s
    x = [mp.mpf(0)] * n
    floor = mp.mpf(10) ** (-(dps - 10))
    for i in range(n - 1, -1, -1):
        if abs(M[i][i]) < floor:
            raise ReferenceError(f"参考求解上三角第 {i} 个对角元过小，矩阵可能奇异")
        x[i] = (y[i] - sum(M[i][j] * x[j] for j in range(i + 1, n))) / M[i][i]
    return x


def reference_solve(A: MatrixInput, B: MatrixInput, dps: int = 120) -> list[list[mp.mpf]]:
    """返回每个右端列的任意精度参考解（mpf 列表的列表）。"""
    with mp.workdps(dps):
        return [
            _householder_solve(A.text, [B.text[i][j] for i in range(B.n_rows)], dps)
            for j in range(B.n_cols)
        ]


def reference_residual(
    A: MatrixInput, x64: list[float], B: MatrixInput, col: int, dps: int = 80
) -> list[mp.mpf]:
    """全程 mpmath 计算的独立残差 r = b - A·x（原矩阵十进制原文）。"""
    with mp.workdps(dps):
        n = A.n_rows
        out = []
        for i in range(n):
            ax = sum(mp.mpf(A.text[i][j]) * mp.mpf(float(x64[j])) for j in range(n))
            out.append(mp.mpf(B.text[i][col]) - ax)
        return out


def reference_backward_error(
    A: MatrixInput, x64: list[float], B: MatrixInput, col: int, dps: int = 80
) -> float:
    """全程 mpmath 计算的分量向后误差，用于独立复核核心模块的判据。"""
    with mp.workdps(dps):
        n = A.n_rows
        r = reference_residual(A, x64, B, col, dps)
        etas = []
        for i in range(n):
            denom = sum(
                abs(mp.mpf(A.text[i][j])) * abs(mp.mpf(float(x64[j]))) for j in range(n)
            ) + abs(mp.mpf(B.text[i][col]))
            ri = abs(r[i])
            if denom > 0:
                etas.append(ri / denom)
            else:
                etas.append(mp.mpf(0) if ri == 0 else mp.inf)
        return float(max(etas))
