"""Structured run logging.

Every estimation run gets a run id; every significant intermediate state
(validation, alignment, split, solve, metrics, failure) is appended as a
JSON record carrying that run id plus the judgment rationale. Records go
to a sink: a file path (JSONL), a writable stream, or an in-memory list
for tests. The log is the replay artifact: given a run id you can
reconstruct what the backend saw and why it concluded what it did.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


def new_run_id() -> str:
    return uuid.uuid4().hex[:16]


@dataclass
class RunRecord:
    run_id: str
    event: str
    payload: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_json(self) -> str:
        return json.dumps(
            {
                "ts": self.timestamp,
                "run_id": self.run_id,
                "event": self.event,
                **self.payload,
            },
            default=_json_default,
        )


def _json_default(value: Any) -> Any:
    try:
        import numpy as np

        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    except ImportError:  # pragma: no cover - numpy is a hard dependency
        pass
    return repr(value)


class RunLogger:
    """Appends RunRecords to a sink callable."""

    def __init__(self, sink: Callable[[RunRecord], None] | None = None) -> None:
        self._sink = sink or (lambda record: None)
        self.records: list[RunRecord] = []

    def log(self, run_id: str, event: str, **payload: Any) -> RunRecord:
        record = RunRecord(run_id=run_id, event=event, payload=payload)
        self.records.append(record)
        self._sink(record)
        return record

    def for_run(self, run_id: str) -> list[RunRecord]:
        return [r for r in self.records if r.run_id == run_id]


def file_sink(path: str) -> Callable[[RunRecord], None]:
    """Sink that appends JSONL records to `path`."""

    def _write(record: RunRecord) -> None:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")

    return _write


def list_sink(target: list[RunRecord]) -> Callable[[RunRecord], None]:
    return target.append


def replay(logger: RunLogger, run_id: str) -> list[dict[str, Any]]:
    """Return the ordered event payloads for one run id."""
    return [
        {"event": r.event, "ts": r.timestamp, **r.payload}
        for r in logger.for_run(run_id)
    ]
