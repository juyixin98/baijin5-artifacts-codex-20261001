"""Chunked job manager.

A job runs the solve pipeline in a background thread; graph construction
streams pairwise terms in row-band chunks (see :mod:`graphcut.graph`) and
polls the cancellation flag at chunk boundaries, so a running job can be
cancelled cooperatively. The job id doubles as the ``run_id`` in the logs,
so any failure can be replayed from the log stream.

State machine::

    PENDING -> RUNNING -> SUCCEEDED
                       -> FAILED      (GraphCutError, category preserved)
                       -> CANCELLED   (cancel requested at a chunk boundary)

Illegal transitions (e.g. cancelling a finished job) raise
:class:`StateConflictError`.
"""

from __future__ import annotations

import enum
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

from .config import AppConfig
from .errors import GraphCutError, NotFoundError, StateConflictError
from .logging_utils import get_logger, log_event
from .models import EnergySpec, SolveResult
from .pipeline import run_pipeline


class JobState(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


@dataclass
class JobRecord:
    job_id: str
    state: JobState = JobState.PENDING
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: SolveResult | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result": result_to_dict(self.result) if self.result else None,
        }


def result_to_dict(result: SolveResult) -> dict:
    cert = result.certificate
    return {
        "run_id": result.run_id,
        "width": result.width,
        "height": result.height,
        "labeling": result.labeling.astype(int).tolist(),
        "energy": {
            "data": result.energy.data,
            "smoothness": result.energy.smoothness,
            "total": result.energy.total,
        },
        "certificate": {
            "flow_value": cert.flow_value,
            "cut_capacity": cert.cut_capacity,
            "graph_constant": cert.graph_constant,
            "seeds_satisfied": cert.seeds_satisfied,
            "scipy_flow_value": cert.scipy_flow_value,
            "consistent": cert.consistent,
            "tolerance": cert.tolerance,
        },
        "stats": result.stats,
    }


class JobManager:
    """Thread-safe registry of chunked solve jobs."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._jobs: dict[str, JobRecord] = {}
        self._cancel_flags: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._logger = get_logger()

    def submit(self, spec: EnergySpec) -> JobRecord:
        job_id = uuid.uuid4().hex[:12]
        record = JobRecord(job_id=job_id)
        flag = threading.Event()
        with self._lock:
            self._jobs[job_id] = record
            self._cancel_flags[job_id] = flag
        thread = threading.Thread(
            target=self._run, args=(record, spec, flag), daemon=True,
            name=f"graphcut-job-{job_id}",
        )
        thread.start()
        return record

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise NotFoundError("unknown_job", f"no job with id {job_id!r}")
        return record

    def cancel(self, job_id: str) -> JobRecord:
        record = self.get(job_id)
        with self._lock:
            if record.state in _TERMINAL:
                raise StateConflictError(
                    "job_already_finished",
                    f"job {job_id} is already {record.state.value} and "
                    "cannot be cancelled",
                    details={"state": record.state.value},
                )
            self._cancel_flags[job_id].set()
        return record

    def _run(
        self,
        record: JobRecord,
        spec: EnergySpec,
        cancel_flag: threading.Event,
    ) -> None:
        record.state = JobState.RUNNING
        try:
            result = run_pipeline(
                spec, self._config,
                run_id=record.job_id,
                should_abort=cancel_flag.is_set,
                logger=self._logger,
            )
        except GraphCutError as exc:
            if exc.code == "job_cancelled":
                record.state = JobState.CANCELLED
            else:
                record.state = JobState.FAILED
            record.error = exc.to_dict()
            log_event(
                self._logger, logging.ERROR, record.job_id, "job_failed",
                category=exc.category.value, code=exc.code,
                reason=exc.message,
            )
        except Exception as exc:  # unexpected: still a computation failure
            record.state = JobState.FAILED
            record.error = {
                "category": "computation",
                "code": "unexpected_error",
                "message": f"{type(exc).__name__}: {exc}",
                "details": {},
            }
            log_event(
                self._logger, logging.ERROR, record.job_id, "job_failed",
                category="computation", code="unexpected_error",
                reason=repr(exc),
            )
        else:
            record.state = JobState.SUCCEEDED
            record.result = result
        finally:
            record.finished_at = time.time()
