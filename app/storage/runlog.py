"""运行日志: 每个请求一个 run_id, 追加 JSONL, 便于问题回放。

三类日志:
* runs.jsonl     一次推理请求的完整生命周期与最终判定
* kernel.trace.jsonl 关键中间状态 (接地规模、论证数、不动点迭代、每条目标状态)
* errors.log     四类错误的分类记录

回放方式: 用 run_id 在 runs 表或 runs.jsonl 中检索, 可拿到原始请求、
中间状态与判断理由, 再用相同请求体重放即可。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLogger:
    def __init__(self, request_log: Path, kernel_trace: Path, error_log: Path) -> None:
        for path in (request_log, kernel_trace, error_log):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.request_log = request_log
        self.kernel_trace = kernel_trace
        self.error_log = error_log

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def log_request_start(self, run_id: str, endpoint: str, request: dict) -> None:
        self._append(
            self.request_log,
            {
                "ts": _utc_now(),
                "run_id": run_id,
                "event": "request_start",
                "endpoint": endpoint,
                "request": request,
            },
        )

    def log_kernel_trace(self, run_id: str, stage: str, state: dict) -> None:
        self._append(
            self.kernel_trace,
            {
                "ts": _utc_now(),
                "run_id": run_id,
                "event": "kernel_trace",
                "stage": stage,
                "state": state,
            },
        )

    def log_request_finish(
        self,
        run_id: str,
        endpoint: str,
        request: dict,
        summary: dict,
        result: Optional[dict],
    ) -> None:
        self._append(
            self.request_log,
            {
                "ts": _utc_now(),
                "run_id": run_id,
                "event": "request_finish",
                "endpoint": endpoint,
                "request": request,
                "summary": summary,
                "result": result,
            },
        )

    def log_error(
        self, run_id: str, endpoint: str, request: dict, error: dict
    ) -> None:
        record = {
            "ts": _utc_now(),
            "run_id": run_id,
            "event": "error",
            "endpoint": endpoint,
            "request": request,
            "error": error,
        }
        self._append(self.error_log, record)
        self._append(self.request_log, record)
