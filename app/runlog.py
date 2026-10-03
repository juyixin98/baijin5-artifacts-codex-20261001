"""Structured run logging.

Every estimation (one-shot or finalized stream) gets a ``run_id``. All key
intermediate states and the reasons for decisions (chosen delay, matrix
shape, numerical rank, regularization, error category) are appended as JSON
records so a failing run can be replayed and audited later.

Records are kept in memory (returned to callers/tests) and optionally
appended to ``<log_dir>/runs.jsonl``.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class RunLogger:
    """Collects structured events for a single run."""

    _file_lock = threading.Lock()

    def __init__(self, log_dir: str | None = None, run_id: str | None = None):
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._log_path = Path(log_dir) / "runs.jsonl" if log_dir else None
        self.records: list[dict[str, Any]] = []

    def log(self, event: str, **data: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **data,
        }
        self.records.append(record)
        if self._log_path is not None:
            line = json.dumps(record, default=_json_default)
            with self._file_lock:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        return record


def _json_default(value: Any) -> Any:
    # numpy scalars / arrays appear in diagnostics; make them serializable.
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)
