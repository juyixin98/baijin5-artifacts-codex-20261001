"""Result provenance: run identity, versions, and step logging.

Every index build and every query produces a :class:`RunInfo` whose
``run_id`` ties together API responses, SQLite metadata rows, and JSONL
log lines. Logs record versions, progress steps, and the judgment basis
of outcomes — an exception is logged as a failure with its category,
never folded into a success record.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy

import app


def environment_versions() -> dict:
    versions = {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "sqlite3": sqlite3.sqlite_version,
        "app": app.__version__,
    }
    try:
        import fastapi

        versions["fastapi"] = fastapi.__version__
    except ImportError:  # library-only usage
        pass
    return versions


def fingerprint_sequences(items: list[tuple[str, str]]) -> str:
    """SHA-256 over (name, sequence) pairs — links a run to its inputs."""
    digest = hashlib.sha256()
    for name, sequence in items:
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(sequence.encode())
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass
class RunInfo:
    """Identity and context of one build/query run."""

    kind: str  # "build" | "query" | "demo" | "test"
    config_fingerprint: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    versions: dict = field(default_factory=environment_versions)
    input_fingerprint: str | None = None
    steps: list[dict] = field(default_factory=list)

    def log_step(self, step: str, **fields) -> None:
        """Append a computation step with its judgment-relevant data."""
        self.steps.append({"step": step, **fields})

    def to_dict(self) -> dict:
        return asdict(self)


class JsonlLogger:
    """Append-only JSONL log; each line carries the run identity."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, run: RunInfo, event: str, **fields) -> None:
        record = {
            "run_id": run.run_id,
            "kind": run.kind,
            "event": event,
            "ts": datetime.now(timezone.utc).isoformat(),
            "versions": run.versions,
            **fields,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
