"""求解器配置：默认值、环境变量覆盖与启动校验。

所有参数均可通过 ``IRSOLVER_`` 前缀的环境变量覆盖，便于验收时按文档复现，
例如 ``IRSOLVER_TOLERANCE=1e-12 IRSOLVER_MP_DPS=80 python -m pytest``。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace

_ENV_PREFIX = "IRSOLVER_"


@dataclass(frozen=True)
class SolverSettings:
    """迭代精化求解器的全部可调参数。"""

    tolerance: float = 1e-10  # 分量向后误差收敛阈值
    stall_ratio: float = 0.5  # 相邻迭代 η 改善不足该比例视为停滞
    fp32_max_iter: int = 12  # fp32 阶段最大校正次数
    fp64_max_iter: int = 8  # fp64 阶段最大校正次数
    mp_max_iter: int = 6  # mpmath 阶段最大校正次数
    mp_dps: int = 60  # mpmath 阶段十进制工作精度
    cond_threshold: float = 1e12  # 条件数超过该值改用高精度 SVD 复核
    reference_dps: int = 120  # 独立参考解工作精度（仅测试/验收对照）

    def __post_init__(self) -> None:
        if not 0.0 < self.tolerance < 1.0:
            raise ValueError(f"tolerance 必须在 (0, 1) 内，得到 {self.tolerance}")
        if not 0.0 < self.stall_ratio < 1.0:
            raise ValueError(f"stall_ratio 必须在 (0, 1) 内，得到 {self.stall_ratio}")
        if min(self.fp32_max_iter, self.fp64_max_iter, self.mp_max_iter) < 1:
            raise ValueError("各阶段最大迭代次数必须为正整数")
        if self.mp_dps < 20:
            raise ValueError(f"mp_dps 至少为 20，得到 {self.mp_dps}")
        if self.cond_threshold <= 1.0:
            raise ValueError(f"cond_threshold 必须大于 1，得到 {self.cond_threshold}")


def _cast(raw: str, current: object) -> object:
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return raw


def load_settings() -> SolverSettings:
    """从环境变量加载配置，未设置的项使用默认值。"""
    base = SolverSettings()
    overrides = {}
    for f in fields(base):
        raw = os.environ.get(_ENV_PREFIX + f.name.upper())
        if raw is not None:
            overrides[f.name] = _cast(raw, getattr(base, f.name))
    return replace(base, **overrides) if overrides else base
