"""测试用独立参考实现:不导入 app 核心,纯 Python 直算,可手算复核。

参考答案必须来自这里或手算字面量,不能由被测核心实现生成。
"""
from __future__ import annotations


def reference_weighted_sum(values: list[int], weights: list[int]) -> int:
    assert len(values) == len(weights)
    total = 0
    for v, w in zip(values, weights):
        total += v * w
    return total


def reference_encode(value: int, n: int) -> int:
    """手算同规则:x mod n(Python 取模恒非负)。"""
    return value % n
