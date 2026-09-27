"""统一错误类型.

四类可区分错误 (验收要求: 输入错误 / 状态冲突 / 资源耗尽 / 计算失败):

* RuleLanguageError      —— 输入错误: 规则/查询文本语法或静态语义有误
* KnowledgeStateError    —— 状态冲突: 存储层状态不合法 (严格规则矛盾、重复写入等)
* ResourceLimitError     —— 资源耗尽: 超过配置的实例数/迭代数/输入规模上限
* ReasoningFailureError  —— 计算失败: 推理内核无法完成计算 (理论上不应发生的内部情形)

API 层依据 category 属性映射到不同的 HTTP 状态码与错误码。
"""
from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    INPUT_ERROR = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILED = "computation_failed"


class ReasonerError(Exception):
    """所有领域错误的基类。"""

    category: ErrorCategory = ErrorCategory.COMPUTATION_FAILED

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.run_id: str | None = None  # 由服务层在捕获时回填, 便于错误回放

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "error": type(self).__name__,
            "message": self.message,
            "details": self.details,
            "run_id": self.run_id,
        }


class RuleLanguageError(ReasonerError):
    """输入错误: 词法/语法错误、作用域错误、优先级引用不存在的规则等。"""

    category = ErrorCategory.INPUT_ERROR


class KnowledgeStateError(ReasonerError):
    """状态冲突: 严格理论不一致、版本冲突、对象不存在等持久化状态问题。"""

    category = ErrorCategory.STATE_CONFLICT


class ResourceLimitError(ReasonerError):
    """资源耗尽: 超过 max_ground_instances / fixpoint_iterations / 输入上限。"""

    category = ErrorCategory.RESOURCE_EXHAUSTED


class ReasoningFailureError(ReasonerError):
    """计算失败: 内核内部不变量被破坏 (例如标签不动点不收敛)。"""

    category = ErrorCategory.COMPUTATION_FAILED
