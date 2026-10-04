"""Run-scoped diagnostic logging.

Every operation runs under a run_id. Log records are JSON lines carrying
the run_id, an event name, optional safe identifiers (key_id, fingerprint),
and a rationale string for judgment calls — enough to replay a failure.

HARD RULE: key material (root key, intermediate chain keys, derived keys)
is never logged. Only key_id (hash of public identity fields) and the
HMAC-based fingerprint may appear. The log-hygiene test scans captured
logs for known key material to enforce this.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, TextIO


def new_run_id() -> str:
    return uuid.uuid4().hex


class RunLogger:
    """Append-only JSONL logger bound to one run_id."""

    def __init__(self, stream: TextIO, run_id: str):
        self._stream = stream
        self.run_id = run_id

    def event(self, name: str, *, rationale: str | None = None, **fields: Any) -> None:
        record = {
            "ts": round(time.time(), 6),
            "run_id": self.run_id,
            "event": name,
        }
        if rationale is not None:
            record["rationale"] = rationale
        record.update(fields)
        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()


def open_log_stream(path: str | Path) -> TextIO:
    return open(path, "a", encoding="utf-8")
