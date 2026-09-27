"""合成测试夹具：良态、病态、奇异与批量右端系统。

所有系统的右端 b 都由"矩阵十进制原文 × 构造的精确解"在高精度（80 位
十进制）下计算得到，因此 x_true 是存储系统的精确解（约 50 位有效数字），
可作为独立于被测核心的对照答案。矩阵本身以 45 位十进制原文存储，
高精度语义不依赖 float64 中间表示。
"""
from __future__ import annotations

from dataclasses import dataclass

import mpmath as mp
import numpy as np
import scipy.linalg as sla

from irsolver.inputs import MatrixInput, parse_matrix

_GEN_DPS = 80  # 夹具生成精度
_TEXT_DIGITS = 45  # 矩阵十进制原文有效数字


@dataclass(frozen=True)
class SystemFixture:
    """一套测试系统：矩阵、右端、逐列精确解（奇异系统为 None）。"""

    name: str
    A: MatrixInput
    B: MatrixInput
    x_true: tuple[tuple[str, ...], ...] | None
    kappa_hint: float | None

    def x_true64(self, col: int = 0) -> np.ndarray:
        assert self.x_true is not None, f"{self.name} 无精确解"
        return np.array([float(v) for v in self.x_true[col]], dtype=np.float64)


def _finish(name: str, A_text: list[list[str]], X_true: list[tuple[str, ...]],
            kappa_hint: float | None) -> SystemFixture:
    """由矩阵原文与各列精确解，用高精度算术生成一致的右端。"""
    A = parse_matrix(A_text, "a")
    n = A.n_rows
    with mp.workdps(_GEN_DPS):
        Amp = A.to_mpmath()
        b_cols = []
        for x_text in X_true:
            xmp = mp.matrix([mp.mpf(v) for v in x_text])
            bmp = Amp * xmp
            b_cols.append([mp.nstr(bmp[i], 50) for i in range(n)])
    B = parse_matrix(
        [[b_cols[j][i] for j in range(len(X_true))] for i in range(n)], "b"
    )
    return SystemFixture(name, A, B, tuple(X_true), kappa_hint)


def _random_A_text(n: int, kappa: float, seed: int) -> list[list[str]]:
    """Q1·diag(σ)·Q2ᵀ 构造指定条件数的矩阵，高精度计算后存十进制原文。"""
    rng = np.random.default_rng(seed)
    q1, _ = np.linalg.qr(rng.standard_normal((n, n)))
    q2, _ = np.linalg.qr(rng.standard_normal((n, n)))
    sigma = np.logspace(0.0, -np.log10(kappa), n)
    with mp.workdps(_GEN_DPS):
        q1m = mp.matrix([[mp.mpf(repr(float(v))) for v in row] for row in q1])
        q2m = mp.matrix([[mp.mpf(repr(float(v))) for v in row] for row in q2])
        am = q1m * mp.diag([mp.mpf(repr(float(s))) for s in sigma]) * q2m.T
        return [[mp.nstr(am[i, j], _TEXT_DIGITS) for j in range(n)] for i in range(n)]


def make_random_system(n: int, kappa: float, seed: int, name: str) -> SystemFixture:
    """指定条件数的可解系统，x_true 为小整数向量。"""
    rng = np.random.default_rng(seed + 1)
    x_true = [tuple(str(int(v)) for v in rng.integers(-5, 6, n))]
    return _finish(name, _random_A_text(n, kappa, seed), x_true, kappa)


def make_hilbert_system(n: int = 12) -> SystemFixture:
    """经典 Hilbert 病态矩阵（κ₂ 约 1.6e16），x_true 为全 1。"""
    with mp.workdps(_GEN_DPS):
        A_text = [
            [mp.nstr(1 / mp.mpf(i + j + 1), _TEXT_DIGITS) for j in range(n)]
            for i in range(n)
        ]
    return _finish(f"hilbert-{n}", A_text, [tuple("1" for _ in range(n))], None)


def make_singular_system(n: int = 8, seed: int = 5) -> SystemFixture:
    """精确奇异系统（秩 n-1）：末行为其余行之和，右端取相容值。"""
    rng = np.random.default_rng(seed)
    rows = [[int(v) for v in rng.integers(-4, 5, n)] for _ in range(n - 1)]
    rows.append([sum(col) for col in zip(*rows)])  # 精确整数运算，秩恰为 n-1
    A_text = [[str(v) for v in row] for row in rows]
    x_any = [str(int(v)) for v in rng.integers(-3, 4, n)]
    A = parse_matrix(A_text, "a")
    with mp.workdps(_GEN_DPS):
        bmp = A.to_mpmath() * mp.matrix([mp.mpf(v) for v in x_any])
        B_text = [[mp.nstr(bmp[i], 50)] for i in range(n)]
    return SystemFixture(
        f"singular-{n}", A, parse_matrix(B_text, "b"), None, None
    )


def make_batch_system(n: int = 8, kappa: float = 1e6, seed: int = 99) -> SystemFixture:
    """批量右端：三列量级差异显著的精确解，验证逐列独立报告。"""
    rng = np.random.default_rng(seed + 1)
    cols = [
        tuple(str(int(v)) for v in rng.integers(-5, 6, n)),
        tuple(f"{int(v)}e8" for v in rng.integers(-5, 6, n)),
        tuple(f"{int(v)}e-8" for v in rng.integers(-5, 6, n)),
    ]
    return _finish("batch", _random_A_text(n, kappa, seed), cols, kappa)


def direct_solve(A: MatrixInput, B: MatrixInput, col: int, dtype) -> np.ndarray:
    """对照组：直接低精度求解（SciPy LU，无精化）。"""
    a = A.to_float64().astype(dtype)
    b = B.to_float64()[:, col].astype(dtype)
    return sla.lu_solve(sla.lu_factor(a), b)


def forward_rel_error(x, x_true_text) -> float:
    """相对精确解的无穷范数相对误差（高精度计算）。"""
    x = [float(v) for v in x]
    with mp.workdps(60):
        num = max(abs(mp.mpf(v) - mp.mpf(t)) for v, t in zip(x, x_true_text))
        den = max(abs(mp.mpf(t)) for t in x_true_text)
        return float(num / den)
