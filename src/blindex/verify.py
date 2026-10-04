"""独立验证层。

不导入 protocol / crypto_adapter / service 的任何实现，
仅依据公开协议规范（README「协议规范」一节）用标准库重算期望结果，
再与被测系统的实际输出比对。参考答案因此不由被测核心实现生成。

- 规范化：独立编写（即使逻辑等价，也是独立代码路径）
- 盲索引：hashlib + hmac（核心实现用的是 PyCryptodome）
- 期望查询结果：对明文夹具直接扫描
"""
from __future__ import annotations

import hashlib
import hmac
import re
import struct
from typing import Optional


def _lp(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _norm_email(v: str) -> str:
    return v.strip().lower()


def _norm_phone(v: str) -> str:
    v = v.strip()
    digits = re.sub(r"[^0-9]", "", v)
    return "+" + digits if v.startswith("+") else digits


def _norm_name(v: str) -> str:
    return " ".join(v.split()).casefold()


def _norm_id_number(v: str) -> str:
    return re.sub(r"[\s\-]+", "", v.strip()).upper()


_NORM = {
    "email": _norm_email,
    "phone": _norm_phone,
    "name": _norm_name,
    "id_number": _norm_id_number,
}


class IndependentVerifier:
    """以明文夹具为基准的独立验证器。"""

    def __init__(self, domain: str, index_bits: int, index_keys: dict[int, bytes]):
        self._domain = domain
        self._bits = index_bits
        self._index_keys = dict(index_keys)

    def normalize(self, field: str, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return _NORM[field](value)

    def expected_index_hex(self, field: str, value: str, version: int) -> str:
        norm = self.normalize(field, value)
        msg = b"BLIDX\x01" + _lp(self._domain) + _lp(field) + _lp(norm)
        digest = hmac.new(
            self._index_keys[version], msg, hashlib.sha256
        ).hexdigest()
        return digest[: self._bits // 4]

    def expected_matches(
        self, fixtures: dict[str, dict], field: str, value: str
    ) -> list[str]:
        """明文扫描得到期望命中集合（独立参考答案）。"""
        target = self.normalize(field, value)
        return sorted(
            rid
            for rid, row in fixtures.items()
            if row.get(field) is not None
            and self.normalize(field, row[field]) == target
        )

    def check_query(
        self,
        fixtures: dict[str, dict],
        field: str,
        value: str,
        actual_ids: list[str],
    ) -> dict:
        """比对实际查询结果与明文参考，输出可解释的验证报告。"""
        expected = self.expected_matches(fixtures, field, value)
        actual = sorted(actual_ids)
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        return {
            "ok": not missing and not unexpected,
            "field": field,
            "expected": expected,
            "actual": actual,
            "missing": missing,        # 应中未中（漏记录）
            "unexpected": unexpected,  # 不应中而中（碰撞未过滤等）
            "note": (
                "盲索引泄露相等关系，是确定性 keyed hash，不是匿名化；"
                "本报告以明文扫描为基准。"
            ),
        }
