"""Unit tests for the chunked job manager state machine."""

import asyncio

import numpy as np
import pytest

from graphcut.config import Settings
from graphcut.errors import (
    ErrorCategory,
    NotFoundError,
    ResourceExhaustedError,
    StateConflictError,
)
from graphcut.jobs import JobManager, JobState


def run(coro):
    return asyncio.run(coro)


def small_spec(spec_factory):
    return spec_factory(
        height=4, width=4,
        unary0=np.linspace(0.1, 1.0, 16).reshape(4, 4),
        unary1=np.linspace(1.0, 0.1, 16).reshape(4, 4),
    )


async def wait_terminal(manager, job, timeout=5.0):
    for _ in range(int(timeout / 0.01)):
        if job.state.terminal:
            return job.state
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job.job_id} did not finish in {timeout}s")


class TestLifecycle:
    def test_success_path(self, spec_factory, test_log):
        async def scenario():
            manager = JobManager(Settings(chunk_rows=1))
            job = manager.submit(small_spec(spec_factory))
            assert job.state is JobState.PENDING
            state = await wait_terminal(manager, job)
            test_log.info("job.finished job_id=%s state=%s run_id=%s",
                          job.job_id, state.value, job.run_id)
            assert state is JobState.SUCCEEDED
            assert job.chunks_done == job.chunks_total == 4
            result = manager.result(job.job_id)
            assert result.certificate.verified
            assert result.run_id == job.run_id
        run(scenario())

    def test_cancel_pending_job(self, spec_factory):
        async def scenario():
            manager = JobManager(Settings(chunk_rows=1))
            job = manager.submit(small_spec(spec_factory))
            # Cancel before the worker task gets a chance to run.
            manager.cancel(job.job_id)
            state = await wait_terminal(manager, job)
            assert state is JobState.CANCELED
            with pytest.raises(StateConflictError):
                manager.result(job.job_id)
        run(scenario())

    def test_cancel_finished_job_is_state_conflict(self, spec_factory):
        async def scenario():
            manager = JobManager(Settings())
            job = manager.submit(small_spec(spec_factory))
            await wait_terminal(manager, job)
            with pytest.raises(StateConflictError) as excinfo:
                manager.cancel(job.job_id)
            err = excinfo.value
            assert err.code == "JOB_ALREADY_FINISHED"
            assert err.category is ErrorCategory.STATE_CONFLICT
        run(scenario())

    def test_result_of_unfinished_job_is_state_conflict(self, spec_factory):
        async def scenario():
            manager = JobManager(Settings())
            job = manager.submit(small_spec(spec_factory))
            with pytest.raises(StateConflictError) as excinfo:
                manager.result(job.job_id)
            assert excinfo.value.code == "JOB_NOT_FINISHED"
            await wait_terminal(manager, job)
        run(scenario())

    def test_unknown_job_is_not_found(self):
        async def scenario():
            manager = JobManager(Settings())
            with pytest.raises(NotFoundError) as excinfo:
                manager.get("job-999999")
            assert excinfo.value.category is ErrorCategory.NOT_FOUND
            with pytest.raises(NotFoundError):
                manager.cancel("job-999999")
        run(scenario())


class TestResourceLimits:
    def test_queue_full_is_resource_exhausted(self, spec_factory, test_log):
        async def scenario():
            manager = JobManager(Settings(max_jobs=1))
            manager.submit(small_spec(spec_factory))  # occupies the only slot
            with pytest.raises(ResourceExhaustedError) as excinfo:
                manager.submit(small_spec(spec_factory))
            err = excinfo.value
            test_log.info("rejected.queue_full code=%s details=%s",
                          err.code, err.details)
            assert err.code == "JOB_QUEUE_FULL"
            assert err.category is ErrorCategory.RESOURCE_EXHAUSTED
            # Drain the first job so the event loop exits cleanly.
            await wait_terminal(manager, manager.get("job-000001"))
        run(scenario())
