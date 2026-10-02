"""Chunked job execution.

A job runs the flood in a background thread, draining the priority heap in
batches of ``chunk_size`` pops. Each finished batch is one progress event
with the job id, so test and service logs show computation steps per run
identity. Chunking never changes the numerical result — only the
granularity of progress reporting.

Failure handling is explicit: a failed job records a stable error category
and message and its status becomes ``failed``; it is never reported as
succeeded.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np

from app.config import Settings
from app.core.watershed import WatershedInputError, flood_watershed
from app.logging_setup import get_logger, log_event


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class JobRecord:
    job_id: str
    run_id: str
    input_sha256: str
    status: JobStatus = JobStatus.QUEUED
    chunks_total_estimate: int = 0
    chunks_done: int = 0
    pixels_processed: int = 0
    pixels_total: int = 0
    current_level: float = 0.0
    error_category: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: Optional[str] = None
    labels: Optional[np.ndarray] = None
    boundary: Optional[np.ndarray] = None
    stats: Optional[dict] = None

    @property
    def progress(self) -> float:
        if self.pixels_total == 0:
            return 0.0
        return min(1.0, self.pixels_processed / self.pixels_total)

    def public_view(self) -> dict:
        view = {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "input_sha256": self.input_sha256,
            "status": self.status.value,
            "progress": round(self.progress, 6),
            "chunks_done": self.chunks_done,
            "pixels_processed": self.pixels_processed,
            "pixels_total": self.pixels_total,
            "current_level": self.current_level,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }
        if self.status is JobStatus.FAILED:
            view["error"] = {"category": self.error_category, "message": self.error_message}
        if self.stats is not None:
            view["stats"] = self.stats
        return view


class JobManager:
    """Thread-pooled-by-demand in-memory job store (single-process service)."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._jobs: "OrderedDict[str, JobRecord]" = OrderedDict()
        self._lock = threading.Lock()
        self._logger = get_logger()

    def submit(
        self,
        elevation: np.ndarray,
        markers: np.ndarray,
        *,
        connectivity: int,
        mask: Optional[np.ndarray],
        chunk_size: Optional[int],
        input_sha256: str,
    ) -> JobRecord:
        record = JobRecord(
            job_id=uuid.uuid4().hex,
            run_id=uuid.uuid4().hex,
            input_sha256=input_sha256,
        )
        with self._lock:
            self._jobs[record.job_id] = record
            while len(self._jobs) > self._settings.max_retained_jobs:
                self._jobs.popitem(last=False)
        worker = threading.Thread(
            target=self._run,
            args=(record, elevation, markers, connectivity, mask, chunk_size),
            name=f"watershed-job-{record.job_id[:8]}",
            daemon=True,
        )
        worker.start()
        return record

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            return self._jobs.get(job_id)

    def _run(self, record, elevation, markers, connectivity, mask, chunk_size) -> None:
        effective_chunk = chunk_size or self._settings.default_chunk_size
        record.status = JobStatus.RUNNING
        log_event(
            self._logger, 20, "job_started",
            job_id=record.job_id, run_id=record.run_id,
            input_sha256=record.input_sha256, chunk_size=effective_chunk,
        )

        def on_progress(chunk_index: int, processed: int, total: int, level: float) -> None:
            record.chunks_done = chunk_index
            record.pixels_processed = processed
            record.pixels_total = total
            record.current_level = level
            log_event(
                self._logger, 20, "job_chunk_done",
                job_id=record.job_id, run_id=record.run_id,
                chunk_index=chunk_index, pixels_processed=processed,
                pixels_total=total, current_level=level,
            )

        try:
            result = flood_watershed(
                elevation, markers,
                connectivity=connectivity, mask=mask,
                chunk_size=effective_chunk, on_progress=on_progress,
            )
        except WatershedInputError as exc:
            record.status = JobStatus.FAILED
            record.error_category = exc.category
            record.error_message = exc.message
            log_event(
                self._logger, 40, "job_failed",
                job_id=record.job_id, run_id=record.run_id,
                error_category=exc.category, error_message=exc.message,
            )
        except Exception as exc:  # never surface an unknown state as success
            record.status = JobStatus.FAILED
            record.error_category = "internal_error"
            record.error_message = f"{type(exc).__name__}: {exc}"
            log_event(
                self._logger, 40, "job_failed",
                job_id=record.job_id, run_id=record.run_id,
                error_category="internal_error", error_message=record.error_message,
            )
        else:
            record.status = JobStatus.SUCCEEDED
            record.labels = result.labels
            record.boundary = result.boundary
            record.stats = result.stats.as_dict()
            log_event(
                self._logger, 20, "job_succeeded",
                job_id=record.job_id, run_id=record.run_id,
                stats=record.stats,
            )
        finally:
            record.finished_at = datetime.now(timezone.utc).isoformat()
