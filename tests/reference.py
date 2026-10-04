"""独立参考实现:只用 Python 标准库,不导入任何被测模块.

测试断言的期望值由这里产生,与被测核心(encoding/protocol)无关,
避免"参考答案由被测实现自身生成"的循环验证。
"""

from __future__ import annotations


def reference_sum(vectors: dict[str, list[float]]) -> list[float]:
    """明文浮点求和参考。定点量化的舍入误差由测试容差吸收,
    容差上界 = 客户端数 * 0.5 / scale。"""
    length = len(next(iter(vectors.values())))
    totals = [0.0] * length
    for vec in vectors.values():
        assert len(vec) == length
        for i, x in enumerate(vec):
            totals[i] += x
    return totals


def quantization_tolerance(n_clients: int, scale: int) -> float:
    """定点编码每客户端每元素最多引入 0.5/scale 的量化误差。"""
    return n_clients * 0.5 / scale + 1e-12
