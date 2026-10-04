"""测试侧的独立明文参考实现。

故意不 import blindex 的任何模块：规范化与期望结果都在这里独立重算，
保证“参考答案不由被测核心实现自身生成”。
"""
from __future__ import annotations

import re
from typing import Optional

# 明文夹具：同值不同表示、NULL、普通值都覆盖
FIXTURES: dict[str, dict] = {
    "fx-alice": {
        "email": "Alice@Example.com",
        "phone": "+1 (555) 010-2030",
        "name": "Alice   Smith",
        "id_number": "ab-123 cd",
    },
    "fx-alice-alias": {
        # 与 fx-alice 同一邮箱/电话/姓名的不同表示
        "email": "  ALICE@example.COM ",
        "phone": "+1-555-010-2030",
        "name": "alice smith",
        "id_number": "AB123CD",
    },
    "fx-bob": {
        "email": "bob@example.org",
        "phone": "+86 138 0000 1111",
        "name": "Bob Jones",
        "id_number": "zz-999",
    },
    "fx-null-contact": {
        "email": None,
        "phone": None,
        "name": "No Contact",
        "id_number": None,
    },
}


def ref_normalize(field: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if field == "email":
        return value.strip().lower()
    if field == "phone":
        v = value.strip()
        digits = re.sub(r"[^0-9]", "", v)
        return "+" + digits if v.startswith("+") else digits
    if field == "name":
        return " ".join(value.split()).casefold()
    if field == "id_number":
        return re.sub(r"[\s\-]+", "", value.strip()).upper()
    raise ValueError(field)


def ref_expected_matches(
    fixtures: dict[str, dict], field: str, value: str
) -> list[str]:
    """明文扫描参考：等值语义作用在规范化后的值上。"""
    target = ref_normalize(field, value)
    return sorted(
        rid
        for rid, row in fixtures.items()
        if row.get(field) is not None and ref_normalize(field, row[field]) == target
    )
