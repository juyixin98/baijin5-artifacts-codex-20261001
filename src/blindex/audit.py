"""审计层：日志仅输出记录身份与安全元数据。

强制手段：detail 键白名单。任何试图把字段值、规范化结果、索引值、
密文片段写进日志的调用都会在写入前被拒绝（fail fast）。
"""
from __future__ import annotations

from .errors import BlindIndexError, Category
from .storage import Storage

#: 允许进入日志的元数据键（记录身份、版本、计数、状态、失败类别）
ALLOWED_DETAIL_KEYS = frozenset(
    {
        "field",            # 字段名（不含值）
        "fields",           # 本次写入涉及的字段名列表
        "versions",         # 查询覆盖的索引密钥版本
        "from_version",
        "to_version",
        "count",
        "candidates",
        "confirmed",
        "rejected",
        "uncertain",
        "processed",
        "remaining",
        "status",
        "category",         # 失败类别
        "state",            # 轮换状态机阶段
    }
)


class AuditLog:
    def __init__(self, storage: Storage):
        self._storage = storage

    def log(
        self,
        op: str,
        request_id: str,
        record_id: str | None = None,
        **detail,
    ) -> None:
        bad = set(detail) - ALLOWED_DETAIL_KEYS
        if bad:
            raise BlindIndexError(
                Category.VALIDATION_ERROR,
                f"审计日志拒绝非白名单键（可能泄露敏感数据）: {sorted(bad)}",
            )
        self._storage.insert_audit(request_id, op, record_id, detail)
