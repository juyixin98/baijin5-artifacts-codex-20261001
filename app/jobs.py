"""Chunked background jobs for multi-seam removal.

A job removes ``num_seams`` seams in chunks of ``chunk_size`` so progress
is observable between chunks.  Job state is explicit: ``pending`` ->
``running`` -> ``succeeded`` | ``failed``.  A failure stores the domain
error category and message; it is never reported as success.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from app.carving import Carver
from app.config import Settings
from app.errors import JobNotFoundError, SeamCarveError
from app.logging_setup import get_logger, log_event

JOB_STATUSES = ("pending", "running", "succeeded", "failed")


@dataclass
class JobState:
    job_id: str
    run_id: str
    status: str = "pending"
    total: int = 0
    done: int = 0
    result: dict | None = None
    error: dict | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "status": self.status,
            "progress": {"done": self.done, "total": self.total},
            "result": self.result,
            "error": self.error,
        }


class JobManager:
    def __init__(self, settings: Settings, logger: logging.Logger | None = None) -> None:
        self._settings = settings
        self._logger = logger or get_logger()
        self._jobs: dict[str, JobState] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    def get(self, job_id: str) -> JobState:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(
                "unknown job id", details={"job_id": job_id}
            ) from None

    def submit(
        self,
        *,
        image,
        mask,
        num_seams: int,
        run_id: str,
        input_sha256: str,
        chunk_size: int | None = None,
        on_seam: Callable[[Any], dict] | None = None,
    ) -> JobState:
        job_id = uuid.uuid4().hex[:16]
        state = JobState(job_id=job_id, run_id=run_id, total=num_seams)
        self._jobs[job_id] = state
        chunk = chunk_size or self._settings.job_chunk_size
        serialize = on_seam or (lambda record: record)
        self._tasks[job_id] = asyncio.create_task(
            self._run(state, image, mask, chunk, input_sha256, serialize)
        )
        return state

    async def _run(self, state, image, mask, chunk, input_sha256, serialize) -> None:
        settings = self._settings
        state.status = "running"
        log_event(
            self._logger,
            logging.INFO,
            "job_started",
            f"job {state.job_id} started: {state.total} seams in chunks of {chunk}",
            run_id=state.run_id,
            job_id=state.job_id,
            input_sha256=input_sha256,
            total=state.total,
            chunk_size=chunk,
        )
        try:
            carver = Carver(
                image,
                mask,
                settings,
                run_id=state.run_id,
                input_sha256=input_sha256,
                logger=self._logger,
            )
            records: list[dict] = []
            while state.done < state.total:
                batch = min(chunk, state.total - state.done)
                for _ in range(batch):
                    record = carver.remove_one()
                    records.append(serialize(record))
                    state.done += 1
                log_event(
                    self._logger,
                    logging.INFO,
                    "job_chunk_completed",
                    f"job {state.job_id}: {state.done}/{state.total} seams removed",
                    run_id=state.run_id,
                    job_id=state.job_id,
                    input_sha256=input_sha256,
                    done=state.done,
                    total=state.total,
                )
                await asyncio.sleep(0)  # yield between chunks
            state.result = {
                "seams": records,
                "final_width": carver.current_width,
            }
            state.status = "succeeded"
            log_event(
                self._logger,
                logging.INFO,
                "job_succeeded",
                f"job {state.job_id} succeeded",
                run_id=state.run_id,
                job_id=state.job_id,
                input_sha256=input_sha256,
                removed=state.done,
            )
        except SeamCarveError as exc:
            state.status = "failed"
            state.error = exc.to_dict()
            log_event(
                self._logger,
                logging.WARNING,
                "job_failed",
                f"job {state.job_id} failed: {exc.category}: {exc.message}",
                run_id=state.run_id,
                job_id=state.job_id,
                input_sha256=input_sha256,
                category=exc.category,
                error=exc.message,
            )
        except Exception as exc:  # never swallow unknown failures
            state.status = "failed"
            state.error = {"category": "INTERNAL", "message": str(exc)}
            self._logger.exception(
                "job %s crashed", state.job_id,
                extra={
                    "event": "job_crashed",
                    "fields": {"run_id": state.run_id, "job_id": state.job_id},
                },
            )
