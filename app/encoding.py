"""协议编码层:有符号整数在 Z_n 上的编码/解码与值域规则。

规则(与 README 的说明一一对应):
- 明文 x 满足 |x| <= max_plaintext_abs,编码为 x mod n(非负剩余)。
- 批次声明总和上界 B < n/2:任何诚实参与下,真实加权和 S 满足 |S| <= B。
- 解码时把剩余映射回以 0 为中心的对称区间 (-n/2, n/2],再检查 |v| <= B。
  若 |v| > B,说明发生了模回绕(有参与者不诚实或上界被突破),
  此时**拒绝**把回绕结果解释成普通负值/正数,抛出 OVERFLOW_DETECTED。
"""
from __future__ import annotations

from .errors import ErrorCategory, ServiceError


def encode_signed(value: int, n: int) -> int:
    """有符号整数 -> Z_n 非负剩余。纯函数,可手算复核。"""
    return value % n


def decode_signed(residue: int, n: int, bound: int) -> int:
    """Z_n 剩余 -> 有符号整数,带总和上界检查。

    residue: [0, n) 的解密原始值
    bound:   批次总和上界 B,要求 B < n/2
    """
    if not 0 <= residue < n:
        raise ServiceError(
            ErrorCategory.VALIDATION,
            f"解密剩余 {residue} 不在 [0, n) 内",
        )
    if bound >= n // 2:
        raise ServiceError(
            ErrorCategory.VALIDATION,
            f"总和上界 {bound} 必须小于 n/2={n // 2},否则无法区分正负",
        )
    v = residue
    if v > n // 2:
        v -= n
    if abs(v) > bound:
        raise ServiceError(
            ErrorCategory.OVERFLOW_DETECTED,
            "解码结果超出批次总和上界,发生模回绕,拒绝解释为普通数值",
            detail={"residue": str(residue), "centered": str(v), "bound": str(bound)},
        )
    return v


def validate_plaintext(value: int, max_plaintext_abs: int) -> None:
    if abs(value) > max_plaintext_abs:
        raise ServiceError(
            ErrorCategory.OUT_OF_RANGE,
            f"明文绝对值 {abs(value)} 超过上限 {max_plaintext_abs}",
        )


def validate_weight(weight: int, max_weight_abs: int) -> None:
    if abs(weight) > max_weight_abs:
        raise ServiceError(
            ErrorCategory.OUT_OF_RANGE,
            f"权重绝对值 {abs(weight)} 超过固定系数范围 ±{max_weight_abs}",
        )
