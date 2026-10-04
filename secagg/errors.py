"""错误分类契约.

四类可区分错误,贯穿所有模块:

- INPUT_ERROR:        输入校验失败(编码越界、消息格式错误、未知客户端等)
- STATE_CONFLICT:     状态冲突(错误阶段、重复提交、活跃集合被篡改等)
- RESOURCE_EXHAUSTED: 资源耗尽(向量过长、客户端数超限、份额数超限等)
- COMPUTATION_FAILURE:计算失败(解密失败、份额重建失败、低于阈值中止等)

每个错误携带 code(机器可读)与 message(人可读,含判断理由),
服务器映射为 HTTP 状态码,测试据此断言失败类别。
"""

from __future__ import annotations

import enum


class ErrorCategory(enum.Enum):
    INPUT_ERROR = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"


class SecAggError(Exception):
    """协议统一异常。category 决定失败类别,code 定位具体原因。"""

    def __init__(
        self,
        category: ErrorCategory,
        code: str,
        message: str,
        detail: dict | None = None,
    ) -> None:
        super().__init__(f"[{category.value}/{code}] {message}")
        self.category = category
        self.code = code
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def input_error(code: str, message: str, **detail) -> SecAggError:
    return SecAggError(ErrorCategory.INPUT_ERROR, code, message, detail)


def state_conflict(code: str, message: str, **detail) -> SecAggError:
    return SecAggError(ErrorCategory.STATE_CONFLICT, code, message, detail)


def resource_exhausted(code: str, message: str, **detail) -> SecAggError:
    return SecAggError(ErrorCategory.RESOURCE_EXHAUSTED, code, message, detail)


def computation_failure(code: str, message: str, **detail) -> SecAggError:
    return SecAggError(ErrorCategory.COMPUTATION_FAILURE, code, message, detail)
