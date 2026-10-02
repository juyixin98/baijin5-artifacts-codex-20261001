"""错误分类契约。

四类可区分错误，每类携带固定的 HTTP 状态码与机器可读类别，
服务端据此生成统一错误响应，测试据此断言失败类别：

- input_error        (422): 请求参数非法（越界区域、未知生成器、非法尺寸）
- state_conflict     (409/404): 状态冲突（层级未发布、重复发布、图像不存在）
- resource_exhausted (413): 资源耗尽（图像过大、区域像素超限）
- compute_failure    (500): 计算失败（瓦片损坏、校验和不匹配、元数据损坏）

损坏瓦片抛出 IntegrityError，绝不以全黑图冒充成功。
"""
from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    INPUT = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTE = "compute_failure"


class PyramidError(Exception):
    """所有领域错误的基类，携带类别与 HTTP 状态码。"""

    category: ErrorCategory = ErrorCategory.COMPUTE
    http_status: int = 500

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


class InputError(PyramidError):
    category = ErrorCategory.INPUT
    http_status = 422


class StateConflictError(PyramidError):
    category = ErrorCategory.STATE_CONFLICT
    http_status = 409


class ImageNotFoundError(StateConflictError):
    http_status = 404


class LevelNotFoundError(StateConflictError):
    """请求的层级未发布（缺失层级不得静默顶替）。"""

    http_status = 404


class ResourceExhaustedError(PyramidError):
    category = ErrorCategory.RESOURCE_EXHAUSTED
    http_status = 413


class ComputeError(PyramidError):
    category = ErrorCategory.COMPUTE
    http_status = 500


class IntegrityError(ComputeError):
    """瓦片内容校验失败：拒绝服务，禁止以全黑图冒充成功。"""
