"""配置与密钥环。

密钥只来自本地密钥文件（scripts/gen_keys.py 生成）或测试临时构造，
源码中不出现任何硬编码密钥。

密钥环同时维护：
- 加密密钥版本集（AES-256，密文随机化）
- 盲索引密钥版本集（HMAC 密钥，轮换期间多版本并存）
- 用途域 domain（盲索引的域分离常量，轮换密钥不变更域）
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .errors import BlindIndexError, Category

DEFAULT_DOMAIN = "local-pii/v1"
DEFAULT_INDEX_BITS = 64
# 下限 4 仅供测试构造强制碰撞；生产配置应 ≥ 64
MIN_INDEX_BITS = 4
MAX_INDEX_BITS = 256
ENC_KEY_LEN = 32   # AES-256
MIN_INDEX_KEY_LEN = 16


@dataclass
class Keyring:
    domain: str
    index_bits: int
    enc_keys: dict[int, bytes]
    current_enc_version: int
    index_keys: dict[int, bytes]
    current_index_version: int

    # -- 序列化 ------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "index_bits": self.index_bits,
            "enc_keys": {str(v): k.hex() for v, k in self.enc_keys.items()},
            "current_enc_version": self.current_enc_version,
            "index_keys": {str(v): k.hex() for v, k in self.index_keys.items()},
            "current_index_version": self.current_index_version,
        }

    @staticmethod
    def from_dict(d: dict) -> "Keyring":
        try:
            kr = Keyring(
                domain=d["domain"],
                index_bits=int(d["index_bits"]),
                enc_keys={int(v): bytes.fromhex(k) for v, k in d["enc_keys"].items()},
                current_enc_version=int(d["current_enc_version"]),
                index_keys={int(v): bytes.fromhex(k) for v, k in d["index_keys"].items()},
                current_index_version=int(d["current_index_version"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise BlindIndexError(Category.CONFIG_ERROR, f"密钥环格式非法: {exc}")
        kr.validate()
        return kr

    def validate(self) -> None:
        if not self.domain:
            raise BlindIndexError(Category.CONFIG_ERROR, "domain 不能为空")
        if not (MIN_INDEX_BITS <= self.index_bits <= MAX_INDEX_BITS) or self.index_bits % 4:
            raise BlindIndexError(
                Category.CONFIG_ERROR,
                f"index_bits 须为 [{MIN_INDEX_BITS},{MAX_INDEX_BITS}] 内 4 的倍数",
            )
        if self.current_enc_version not in self.enc_keys:
            raise BlindIndexError(Category.CONFIG_ERROR, "当前加密密钥版本不在密钥环中")
        if self.current_index_version not in self.index_keys:
            raise BlindIndexError(Category.CONFIG_ERROR, "当前索引密钥版本不在密钥环中")
        for v, k in self.enc_keys.items():
            if len(k) != ENC_KEY_LEN:
                raise BlindIndexError(
                    Category.CONFIG_ERROR, f"加密密钥 v{v} 长度须为 {ENC_KEY_LEN} 字节"
                )
        for v, k in self.index_keys.items():
            if len(k) < MIN_INDEX_KEY_LEN:
                raise BlindIndexError(
                    Category.CONFIG_ERROR, f"索引密钥 v{v} 长度须 ≥ {MIN_INDEX_KEY_LEN} 字节"
                )


def generate_keyring(
    domain: str = DEFAULT_DOMAIN, index_bits: int = DEFAULT_INDEX_BITS
) -> Keyring:
    """生成全新密钥环（加密/索引密钥各自独立，均取自安全随机源）。"""
    kr = Keyring(
        domain=domain,
        index_bits=index_bits,
        enc_keys={1: secrets.token_bytes(ENC_KEY_LEN)},
        current_enc_version=1,
        index_keys={1: secrets.token_bytes(32)},
        current_index_version=1,
    )
    kr.validate()
    return kr


def load_keyring(path: str | Path) -> Keyring:
    p = Path(path)
    if not p.exists():
        raise BlindIndexError(
            Category.CONFIG_ERROR,
            f"密钥文件不存在: {p}（先运行 python scripts/gen_keys.py）",
        )
    return Keyring.from_dict(json.loads(p.read_text(encoding="utf-8")))


def save_keyring(keyring: Keyring, path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keyring.to_dict(), indent=2), encoding="utf-8")
