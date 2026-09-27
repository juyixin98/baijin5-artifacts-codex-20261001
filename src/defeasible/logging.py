"""Structured run logging.

Every evaluation gets a ``run_id`` and appends JSONL records that capture
enough to *replay* the problem: the inputs (case, theory fingerprint,
query), key intermediate state (ground-rule count, fixpoint rounds, status
counters), the final judgement with its reason codes, and -- on failure --
the exact failure category.

Records are append-only; a corrupt tail line never prevents later runs from
being written.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import Evaluation, QueryResult, Status


def new_run_id() -> str:
    return "run-" + uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunLogger:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._buffer: list[dict[str, Any]] = []

    def _write(self, record: dict[str, Any]) -> None:
        record = {"ts": _now(), **record}
        if self._path == ":memory:":
            self._buffer.append(record)
            return
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    # ----- event types -----

    def log_start(
        self, run_id: str, case_id: str, query: str | None
    ) -> None:
        self._write(
            {
                "event": "start",
                "run_id": run_id,
                "case_id": case_id,
                "query": query,
            }
        )

    def log_intermediate(
        self, run_id: str, evaluation: Evaluation
    ) -> None:
        """Key intermediate state produced by the kernel."""
        status_counts = Counter(
            st.value for st in evaluation.conclusions.values()
        )
        self._write(
            {
                "event": "intermediate",
                "run_id": run_id,
                "theory_fingerprint": evaluation.theory_fingerprint,
                "domain": list(evaluation.domain),
                "evidence": [e.literal for e in evaluation.evidence],
                "ground_rule_count": evaluation.ground_rule_count,
                "rounds_used": evaluation.rounds_used,
                "candidate_literals": len(evaluation.conclusions),
                "status_counts": dict(status_counts),
            }
        )

    def log_result(self, run_id: str, result: QueryResult) -> None:
        by_kind: dict[str, list[str]] = {"support": [], "defeat": [], "pending": []}
        for chain in result.chains:
            by_kind[chain.kind.value].append(chain.reason)
        self._write(
            {
                "event": "result",
                "run_id": run_id,
                "query": result.literal.literal,
                "status": result.status.value,
                "flags": {
                    "definite": result.definite,
                    "supported": result.supported,
                    "opposite_supported": result.opposite_supported,
                },
                "chain_counts": {k: len(v) for k, v in by_kind.items()},
                # judgement rationale, one line per chain
                "reasons": by_kind["support"]
                + by_kind["defeat"]
                + by_kind["pending"],
            }
        )

    def log_error(self, run_id: str, error: Any) -> None:
        self._write(
            {
                "event": "error",
                "run_id": run_id,
                "error_code": getattr(error, "code", "internal_error"),
                "message": str(error),
                "details": getattr(error, "details", {}),
            }
        )

    # ----- replay helpers -----

    def records(self) -> list[dict[str, Any]]:
        if self._path == ":memory:":
            return list(self._buffer)
        if not Path(self._path).exists():
            return []
        out: list[dict[str, Any]] = []
        with open(self._path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        # skip a torn tail line rather than losing all history
                        continue
        return out

    def records_for(self, run_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records() if r.get("run_id") == run_id]
