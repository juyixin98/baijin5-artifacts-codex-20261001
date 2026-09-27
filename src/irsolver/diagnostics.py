"""诊断模块：请求标识、结构化日志、决策记录与脱敏摘要。

每次求解分配请求标识，贯穿日志与响应；决策日志记录为什么接受、拒绝、
升级精度或判定奇异。敏感模式下日志只输出事件与状态，不输出 κ、η、范数
等数值画像，矩阵摘要以形状 + SHA-256 指纹代替。
"""
from __future__ import annotations

import hashlib
import logging
import uuid

import numpy as np

from .inputs import MatrixInput

_MAX_JOURNAL_ENTRIES = 500
_LOGGER_NAME = "irsolver"
# 敏感模式下允许出现在日志中的键（不含任何数值画像）
_SAFE_LOG_KEYS = frozenset(
    {"tier", "status", "iteration", "reason", "event", "column", "from_tier", "method"}
)


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


def get_logger(request_id: str) -> logging.LoggerAdapter:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logging.LoggerAdapter(logger, {"request_id": request_id})


class DecisionJournal:
    """记录求解过程中的关键决策，同时输出到结构化日志。"""

    def __init__(self, request_id: str, sensitive: bool):
        self.request_id = request_id
        self.sensitive = sensitive
        self._entries: list[dict] = []
        self._log = get_logger(request_id)

    def record(self, event: str, column: int | None = None, **detail: object) -> None:
        entry: dict = {"event": event, "request_id": self.request_id}
        if column is not None:
            entry["column"] = column
        entry.update(detail)
        if len(self._entries) < _MAX_JOURNAL_ENTRIES:
            self._entries.append(entry)
        if self.sensitive:
            log_detail = {k: v for k, v in detail.items() if k in _SAFE_LOG_KEYS}
        else:
            log_detail = detail
        self._log.info("%s %s", event, log_detail)

    @property
    def entries(self) -> list[dict]:
        return list(self._entries)


def fingerprint(*matrices: MatrixInput) -> str:
    """矩阵十进制原文的 SHA-256 指纹（截断 16 位），用于脱敏标识。"""
    h = hashlib.sha256()
    for m in matrices:
        for row in m.text:
            h.update(",".join(row).encode())
            h.update(b";")
    return h.hexdigest()[:16]


def matrix_summary(
    A: MatrixInput, B: MatrixInput, kappa: float | None, sensitive: bool
) -> dict:
    """矩阵摘要。敏感模式只暴露形状与指纹，不暴露范数、条件数等数值画像。"""
    summary: dict = {
        "shape_a": [A.n_rows, A.n_cols],
        "shape_b": [B.n_rows, B.n_cols],
        "fingerprint": fingerprint(A, B),
    }
    if sensitive:
        return summary
    summary["norm_a_fro"] = float(np.linalg.norm(A.to_float64(), "fro"))
    summary["condition_estimate"] = kappa
    return summary
