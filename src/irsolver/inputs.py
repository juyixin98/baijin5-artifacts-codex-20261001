"""数值输入模块：解析、校验与规范化矩阵/右端项。

输入统一规范化为十进制原文（字符串），之后任意精度的转换都从原文出发，
避免"先转 float64 再转高精度"造成的输入信息丢失。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

import mpmath as mp
import numpy as np

MAX_DIMENSION = 512  # 稠密任意精度算法的实用上限，超出拒绝服务


class InputValidationError(ValueError):
    """输入校验失败。reason 为机器可读类别，detail 为人可读说明。"""

    def __init__(self, reason: str, detail: str):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")


@dataclass(frozen=True)
class MatrixInput:
    """规范化后的矩阵：保留十进制原文，是"原矩阵"的唯一权威表示。"""

    text: tuple[tuple[str, ...], ...]
    n_rows: int
    n_cols: int

    def to_float64(self) -> np.ndarray:
        return np.array(
            [[float(v) for v in row] for row in self.text], dtype=np.float64
        )

    def to_mpmath(self) -> mp.matrix:
        return mp.matrix([[mp.mpf(v) for v in row] for row in self.text])


def _canonicalize(value: Any, where: str) -> str:
    """把单个输入项规范化为十进制原文字符串，拒绝非数值与非有限值。"""
    if isinstance(value, bool):
        raise InputValidationError("invalid_entry", f"{where} 不接受布尔值")
    if isinstance(value, Decimal):
        dec = value
    elif isinstance(value, int):
        dec = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise InputValidationError("non_finite", f"{where} 含非有限值 {value!r}")
        dec = Decimal(str(value))
    elif isinstance(value, str):
        try:
            dec = Decimal(value.strip())
        except InvalidOperation:
            raise InputValidationError(
                "invalid_entry", f"{where} 无法解析为数值: {value!r}"
            ) from None
    else:
        raise InputValidationError(
            "invalid_entry", f"{where} 类型不支持: {type(value).__name__}"
        )
    if not dec.is_finite():
        raise InputValidationError("non_finite", f"{where} 含非有限值 {value!r}")
    return str(dec)


def parse_matrix(data: Any, name: str) -> MatrixInput:
    """校验并规范化一个二维矩阵输入。"""
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)) or not data:
        raise InputValidationError("invalid_shape", f"{name} 必须是非空二维数组")
    rows: list[tuple[str, ...]] = []
    width: int | None = None
    for i, row in enumerate(data):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)) or not row:
            raise InputValidationError("invalid_shape", f"{name} 第 {i} 行不是非空数组")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise InputValidationError(
                "ragged", f"{name} 第 {i} 行长度 {len(row)} 与首行长度 {width} 不一致"
            )
        rows.append(
            tuple(_canonicalize(v, f"{name}[{i}][{j}]") for j, v in enumerate(row))
        )
    if len(rows) > MAX_DIMENSION or (width or 0) > MAX_DIMENSION:
        raise InputValidationError(
            "too_large", f"{name} 维度超过上限 {MAX_DIMENSION}"
        )
    return MatrixInput(tuple(rows), len(rows), width or 0)


def parse_system(a_data: Any, b_data: Any) -> tuple[MatrixInput, MatrixInput]:
    """校验整个线性系统：A 为方阵，B 行数与 A 阶数一致、至少一列。"""
    A = parse_matrix(a_data, "a")
    if A.n_rows != A.n_cols:
        raise InputValidationError(
            "not_square", f"系数矩阵必须为方阵，得到 {A.n_rows}x{A.n_cols}"
        )
    B = parse_matrix(b_data, "b")
    if B.n_rows != A.n_rows:
        raise InputValidationError(
            "dimension_mismatch", f"b 行数 {B.n_rows} 与 a 阶数 {A.n_rows} 不一致"
        )
    return A, B
