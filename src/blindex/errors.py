"""失败类别定义。

所有可预期的失败都归入明确类别，接口与测试按类别断言，
不允许“接口能调用就算通过”。
"""
from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"          # 输入不合法（未知字段、类型错误）
    NULL_QUERY = "NULL_QUERY"                      # 对 NULL 值发起等值查询，按规范拒绝
    ENVELOPE_MALFORMED = "ENVELOPE_MALFORMED"      # 密文信封结构损坏
    UNKNOWN_KEY_VERSION = "UNKNOWN_KEY_VERSION"    # 信封/索引引用了不存在的密钥版本
    DECRYPT_AUTH_FAILED = "DECRYPT_AUTH_FAILED"    # GCM 认证失败（篡改或密钥错误）
    NOT_FOUND = "NOT_FOUND"                        # 记录不存在
    ROTATION_STATE_ERROR = "ROTATION_STATE_ERROR"  # 轮换状态机非法迁移
    CONFIG_ERROR = "CONFIG_ERROR"                  # 配置/密钥环不合法


class BlindIndexError(Exception):
    """携带失败类别的业务异常。"""

    def __init__(self, category: Category, message: str):
        super().__init__(message)
        self.category = category
        self.message = message

    def to_dict(self) -> dict:
        return {"status": "error", "category": self.category.value, "detail": self.message}
