"""Chunked background job manager.

Jobs run on the asyncio event loop: the s-t graph is built in row chunks
(yielding to the loop and honouring cancellation between chunks), and the
CPU-bound max-flow solve is delegated to a worker thread.

State machine::

    PENDING -> RUNNING -> SUCCEEDED
                       -> FAILED
    PENDING/RUNNING -> CANCELED

Illegal transitions are reported as STATE_CONFLICT (e.g. cancelling a
finished job, fetching the result of an unfinished one).
"""

from __future__ import annotations

import asyncio
import enum
import itertools
import time
from dataclasses import dataclass, field

from .config import Settings
from .contracts import SegmentationSpec
from .errors import (
    GraphCutError,
    NotFoundError,
    ResourceExhaustedError,
    StateConflictError,
)
from .runlog import RunLogger, new_run_id
from .service import BuildCancelled, SegmentationResult, run_segmentation


class JobState(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"

    @property
    def terminal(self) -> bool:
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED)


@dataclass(frozen=True)
class JobError:
    category: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"category": self.category, "code": self.code, "message": self.message}


@dataclass
class Job:
    job_id: str
    run_id: str
    spec: SegmentationSpec
    state: JobState = JobState.PENDING
    chunks_done: int = 0
    chunks_total: int = 0
    result: SegmentationResult | None = None
    error: JobError | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task | None = field(default=None, repr=False)

    def view(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "state": self.state.value,
            "progress": {
                "chunks_done": self.chunks_done,
                "chunks_total": self.chunks_total,
            },
            "error": self.error.to_dict() if self.error else None,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    """In-memory job registry with bounded capacity."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._jobs: dict[str, Job] = {}
        self._counter = itertools.count(1)

    # ------------------------------------------------------------------ API
    def submit(self, spec: SegmentationSpec) -> Job:
        active = sum(1 for j in self._jobs.values() if not j.state.terminal)
        if active >= self.settings.max_jobs:
            raise ResourceExhaustedError(
                f"job queue full: {active} active jobs, limit {self.settings.max_jobs}",
                code="JOB_QUEUE_FULL",
                details={"active": active, "limit": self.settings.max_jobs},
            )
        job = Job(
            job_id=f"job-{next(self._counter):06d}",
            run_id=new_run_id(),
            spec=spec,
        )
        self._jobs[job.job_id] = job
        job._task = asyncio.ensure_future(self._run(job))
        RunLogger(job.run_id).state("job.submitted", job_id=job.job_id,
                                    pixels=spec.num_pixels)
        return job

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError(
                f"job {job_id!r} does not exist",
                code="JOB_NOT_FOUND",
                details={"job_id": job_id},
            )
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.state.terminal:
            raise StateConflictError(
                f"job {job_id!r} is already {job.state.value} and cannot be cancelled",
                code="JOB_ALREADY_FINISHED",
                details={"job_id": job_id, "state": job.state.value},
            )
        job._cancel.set()
        return job

    def result(self, job_id: str) -> SegmentationResult:
        job = self.get(job_id)
        if job.state is not JobState.SUCCEEDED:
            raise StateConflictError(
                f"job {job_id!r} is {job.state.value}; results are only "
                "available for SUCCEEDED jobs",
                code="JOB_NOT_FINISHED" if not job.state.terminal else "JOB_FAILED_STATE",
                details={"job_id": job_id, "state": job.state.value},
            )
        assert job.result is not None
        return job.result

    # -------------------------------------------------------------- internals
    async def _run(self, job: Job) -> None:
        log = RunLogger(job.run_id)
        job.state = JobState.RUNNING
        try:
            result = await self._execute(job)
        except BuildCancelled:
            job.state = JobState.CANCELED
            job.finished_at = time.time()
            log.decision("job.canceled", reason="cancel requested during graph build",
                         job_id=job.job_id, chunks_done=job.chunks_done)
            return
        except GraphCutError as exc:
            job.state = JobState.FAILED
            job.error = JobError(category=exc.category.value, code=exc.code,
                                 message=exc.message)
            job.finished_at = time.time()
            log.failure("job.failed", job_id=job.job_id, category=exc.category.value,
                        code=exc.code, message=exc.message)
            return
        except Exception as exc:  # pragma: no cover - defensive
            job.state = JobState.FAILED
            job.error = JobError(category="COMPUTATION_FAILURE",
                                 code="UNEXPECTED_FAILURE", message=str(exc))
            job.finished_at = time.time()
            log.failure("job.failed", job_id=job.job_id,
                        category="COMPUTATION_FAILURE", code="UNEXPECTED_FAILURE",
                        message=str(exc))
            return
        job.result = result
        job.state = JobState.SUCCEEDED
        job.finished_at = time.time()
        log.state("job.succeeded", job_id=job.job_id,
                  energy=result.certificate.energy.total)

    async def _execute(self, job: Job) -> SegmentationResult:
        def on_progress(done: int, total: int) -> None:
            job.chunks_done, job.chunks_total = done, total

        def should_cancel() -> bool:
            return job._cancel.is_set()

        # The graph build is chunked and cancellation-aware; run it (and the
        # solve) in a worker thread so the event loop stays responsive, while
        # still polling cancellation between chunks.
        return await asyncio.to_thread(
            run_segmentation,
            job.spec,
            settings=self.settings,
            run_id=job.run_id,
            chunk_rows=self.settings.chunk_rows,
            on_progress=on_progress,
            should_cancel=should_cancel,
        )
