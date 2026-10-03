"""Structured per-run logging.

Every mutating operation gets a ``run_id``; events are appended as JSON lines
to ``<log_dir>/run-<run_id>.jsonl`` so a failing scenario can be replayed from
its logged inputs, intermediate state (weight norms, error RMS, freeze flags)
and the recorded decision rationale.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class RunLog:
    def __init__(self, log_dir: str, run_id: str) -> None:
        self.run_id = run_id
        os.makedirs(log_dir, exist_ok=True)
        self._path = os.path.join(log_dir, f"run-{run_id}.jsonl")

    @property
    def path(self) -> str:
        return self._path

    def event(self, event: str, **fields: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=_json_default) + "\n")


def _json_default(value: Any) -> Any:
    # numpy scalars/arrays appear in numeric fields; degrade gracefully.
    if hasattr(value, "item"):
        return value.item()
    if hasattr(value, "tolist"):
        return value.tolist()
    return str(value)
