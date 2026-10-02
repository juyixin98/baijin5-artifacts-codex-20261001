"""In-memory job store.  Jobs are immutable-ish records updated in place."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Status vocabulary.  Nothing outside these values is ever reported, and
# FAILED always carries an error category — unknown states are never
# reported as success.
PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"

STAGE_ORDER = ("validate", "flood", "extract_boundary", "finalize")


@dataclass
class StageRecord:
    name: str
    started_at: str
    duration_ms: float | None = None


@dataclass
class JobRecord:
    job_id: str
    request: dict
    status: str = PENDING
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    input_sha256: str | None = None
    stages: list[StageRecord] = field(default_factory=list)
    progress: list[dict] = field(default_factory=list)
    result: dict | None = None
    error: dict | None = None

    def to_dict(self, include_result: bool = True) -> dict:
        payload = {
            "job_id": self.job_id,
            "status": self.status,
            "created_at": self.created_at,
            "input_sha256": self.input_sha256,
            "stages": [
                {"name": s.name, "started_at": s.started_at, "duration_ms": s.duration_ms}
                for s in self.stages
            ],
            "progress": list(self.progress),
            "error": self.error,
        }
        if include_result:
            payload["result"] = self.result
        return payload


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, JobRecord] = {}

    def create(self, request: dict) -> JobRecord:
        record = JobRecord(job_id=uuid.uuid4().hex, request=request)
        with self._lock:
            self._jobs[record.job_id] = record
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)
