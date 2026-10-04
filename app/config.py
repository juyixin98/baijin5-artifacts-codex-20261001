"""配置层:所有可调参数集中在此,支持环境变量覆盖。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

ENV_PREFIX = "PAGG_"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(ENV_PREFIX + name)
    return int(raw) if raw else default


@dataclass(frozen=True)
class Settings:
    """运行参数。

    db_path:           SQLite 文件路径
    key_size:          Paillier 模数位数(本地测试可用 1024 提速,建议 >=2048)
    max_plaintext_abs: 单个明文绝对值上界 |x| <= max_plaintext_abs
    max_weight_abs:    权重(系数)绝对值上界 |w| <= max_weight_abs,范围固定
    fernet_key:        私钥落盘加密密钥(base64 urlsafe);缺省时进程内临时生成,
                       重启后历史批次私钥不可解(仅本地测试可接受,README 有说明)
    """

    db_path: str
    key_size: int = 2048
    max_plaintext_abs: int = 2**31 - 1
    max_weight_abs: int = 2**15
    fernet_key: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get(ENV_PREFIX + "DB_PATH", "pagg.db"),
            key_size=_env_int("KEY_SIZE", 2048),
            max_plaintext_abs=_env_int("MAX_PLAINTEXT_ABS", 2**31 - 1),
            max_weight_abs=_env_int("MAX_WEIGHT_ABS", 2**15),
            fernet_key=os.environ.get(ENV_PREFIX + "FERNET_KEY") or None,
        )
