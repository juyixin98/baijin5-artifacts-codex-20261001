"""Job records: every conversion attempt leaves an auditable trail."""
from __future__ import annotations

import enum
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class JobStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNDECIDABLE = "undecidable"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class Decision:
    """One logged decision: why a request was accepted/rejected/undecidable."""

    outcome: str  # "accepted" | "rejected" | "undecidable"
    reason: str
    category: str | None = None  # FailureCategory value when rejected


@dataclass
class JobRecord:
    job_id: str
    request_id: str
    status: JobStatus
    created_at: float
    decisions: list[Decision] = field(default_factory=list)
    report: dict[str, Any] | None = None
    result: Any = None  # ImageData, kept in-memory only

    def summary(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "request_id": self.request_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "decisions": [
                {
                    "outcome": d.outcome,
                    "reason": d.reason,
                    "category": d.category,
                }
                for d in self.decisions
            ],
            "report": self.report,
        }


class JobStore:
    """In-memory job store (bounded, thread-safe)."""

    def __init__(self, capacity: int = 1024) -> None:
        self._capacity = capacity
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    @staticmethod
    def new_ids() -> tuple[str, str]:
        return uuid.uuid4().hex, uuid.uuid4().hex[:12]

    def put(self, record: JobRecord) -> None:
        with self._lock:
            if len(self._jobs) >= self._capacity:
                oldest = min(self._jobs.values(), key=lambda r: r.created_at)
                del self._jobs[oldest.job_id]
            self._jobs[record.job_id] = record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def new_record(self, request_id: str) -> JobRecord:
        record = JobRecord(
            job_id=uuid.uuid4().hex,
            request_id=request_id,
            status=JobStatus.ACCEPTED,
            created_at=time.time(),
        )
        self.put(record)
        return record
