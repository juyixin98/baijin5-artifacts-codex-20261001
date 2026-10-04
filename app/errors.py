"""统一错误模型:所有可预期失败都有明确类别,绝不把异常吞成成功。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCategory(str, Enum):
    VALIDATION = "VALIDATION"                    # 请求格式/参数非法
    NOT_FOUND = "NOT_FOUND"                      # 批次或聚合产物不存在
    KEY_MISMATCH = "KEY_MISMATCH"                # 密钥指纹与批次绑定不一致
    OUT_OF_RANGE = "OUT_OF_RANGE"                # 明文/权重/密文超出声明值域
    OVERFLOW_RISK = "OVERFLOW_RISK"              # 提交将使最坏情况总和上界被突破
    OVERFLOW_DETECTED = "OVERFLOW_DETECTED"      # 解码时发现真值越界(模回绕,拒绝解释)
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"  # 算法本身不支持的运算
    INTERNAL = "INTERNAL"                        # 未预期内部错误


# 类别 -> HTTP 状态码
STATUS_MAP = {
    ErrorCategory.VALIDATION: 400,
    ErrorCategory.NOT_FOUND: 404,
    ErrorCategory.KEY_MISMATCH: 409,
    ErrorCategory.OUT_OF_RANGE: 422,
    ErrorCategory.OVERFLOW_RISK: 422,
    ErrorCategory.OVERFLOW_DETECTED: 409,
    ErrorCategory.UNSUPPORTED_OPERATION: 400,
    ErrorCategory.INTERNAL: 500,
}


class ServiceError(Exception):
    """携带失败类别的业务异常。接口层据此返回结构化错误,而不是 200。"""

    def __init__(self, category: ErrorCategory, message: str,
                 detail: Optional[Any] = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }
