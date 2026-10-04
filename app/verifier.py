"""独立验证层:不依赖被测核心实现的明文参考与判定。

reference_weighted_sum 是纯 Python 直算,可手算复核;
测试中的参考答案由它或更朴素的手算字面量给出,绝不从 app 核心反推。
"""
from __future__ import annotations

from enum import Enum
from typing import Iterable, Optional, Tuple


class Verdict(str, Enum):
    MATCH = "MATCH"                                # 解密结果与明文参考一致
    MISMATCH = "MISMATCH"                          # 解密结果与明文参考不一致
    REFERENCE_OVERFLOW = "REFERENCE_OVERFLOW"      # 参考和本身越界,比较无意义
    DECRYPT_OVERFLOW = "DECRYPT_OVERFLOW"          # 解密解码阶段已判定模回绕


def reference_weighted_sum(pairs: Iterable[Tuple[int, int]]) -> int:
    """朴素明文加权和:sum(v * w)。独立实现,供比对。"""
    total = 0
    for value, weight in pairs:
        total += value * weight
    return total


def judge(decrypted: Optional[int], reference: int, bound: int) -> dict:
    """比对解密结果与明文参考,返回结构化判定(判定依据随结果返回)。"""
    if abs(reference) > bound:
        return {
            "verdict": Verdict.REFERENCE_OVERFLOW.value,
            "reason": f"参考和 |{reference}| 超过批次上界 {bound},比较无意义",
            "expected": str(reference),
            "actual": None if decrypted is None else str(decrypted),
        }
    if decrypted is None:
        return {
            "verdict": Verdict.DECRYPT_OVERFLOW.value,
            "reason": "解密解码阶段判定模回绕,无可比对的明文结果",
            "expected": str(reference),
            "actual": None,
        }
    if decrypted == reference:
        return {
            "verdict": Verdict.MATCH.value,
            "reason": "解密结果与独立明文参考完全一致",
            "expected": str(reference),
            "actual": str(decrypted),
        }
    return {
        "verdict": Verdict.MISMATCH.value,
        "reason": "解密结果与明文参考不一致(可能混入其他密钥的密文或数据被篡改)",
        "expected": str(reference),
        "actual": str(decrypted),
    }
