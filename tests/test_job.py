"""Job engine tests: execution, interrupt/resume, digest binding, write
disjointness, and run-log attribution."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from tileconv.contract import BoundaryMode, KernelSpec
from tileconv.errors import DigestMismatchError, InjectedInterrupt, JobStateError
from tileconv.fixtures import synthesize
from tileconv.job import TiledJob
from tileconv.kernel import direct_reference
from tileconv.logging_utils import get_run_logger
from tileconv.storage import ImageStore


def make_job(store: ImageStore, settings, kind="random", shape=(37, 29),
             tile=(16, 16), kernel=None, boundary=BoundaryMode.MIRROR,
             cval=0.0, seed=5) -> TiledJob:
    img = synthesize(kind, shape, seed=seed)
    spec = store.create_image(img, meta={"kind": kind, "seed": seed})
    kernel = kernel or KernelSpec.dense(np.array([[1.0, 2.0, 1.0],
                                                  [2.0, 4.0, 2.0],
                                                  [1.0, 2.0, 1.0]]) / 16.0)
    return TiledJob.create(store, settings.jobs_dir, spec, kernel, boundary,
                           cval=cval, tile_shape=tile,
                           logs_dir=settings.logs_dir)


class TestRunToCompletion:
    def test_output_matches_direct_reference(self, store, settings):
        job = make_job(store, settings)
        result = job.run()
        assert result["status"] == "completed"
        assert job.progress["fraction"] == 1.0
        img = store.open_array(job.state["spec"]["image_id"])
        expected = direct_reference(img, job.kernel_spec(), BoundaryMode.MIRROR)
        np.testing.assert_allclose(job.open_output()[:], expected, atol=1e-10)

    def test_completed_job_cannot_rerun(self, store, settings):
        job = make_job(store, settings)
        job.run()
        with pytest.raises(JobStateError):
            job.run()


class TestWriteDisjointness:
    def test_each_region_written_exactly_once(self, store, settings):
        job = make_job(store, settings, shape=(40, 33), tile=(16, 16))
        written = []
        job.run(on_tile_written=lambda t: written.append(
            (t.row0, t.row1, t.col0, t.col1)))
        cover = np.zeros((40, 33), dtype=np.int32)
        for r0, r1, c0, c1 in written:
            cover[r0:r1, c0:c1] += 1
        assert cover.min() == 1 and cover.max() == 1
        assert len(written) == len(set(written))


class TestInterruptResume:
    def test_interrupt_then_resume_matches_uninterrupted(self, store, settings):
        kernel = KernelSpec.dense(np.arange(1, 10, dtype=np.float64).reshape(3, 3))

        job_a = make_job(store, settings, kernel=kernel, seed=1)
        with pytest.raises(InjectedInterrupt) as exc:
            job_a.run(fail_after=2)
        assert exc.value.category == "InterruptInjected"
        assert job_a.status == "interrupted"
        done = sum(1 for t in job_a.state["tiles"] if t["status"] == "done")
        assert done == 2

        # resume in a fresh job object (simulates process restart)
        job_a2 = TiledJob(job_a.state_path, store)
        result = job_a2.resume()
        assert result["status"] == "completed"

        job_b = make_job(store, settings, kernel=kernel, seed=1)
        job_b.run()
        np.testing.assert_array_equal(job_a2.open_output()[:],
                                      job_b.open_output()[:])

    def test_resume_rejects_changed_input(self, store, settings):
        job = make_job(store, settings)
        with pytest.raises(InjectedInterrupt):
            job.run(fail_after=1)
        # tamper with the input image on disk
        img = store.open_array(job.state["spec"]["image_id"], writable=True)
        img[0, 0] += 1.0
        img.flush()
        with pytest.raises(DigestMismatchError) as exc:
            TiledJob(job.state_path, store).resume()
        assert exc.value.category == "DigestMismatch"
        assert "image" in exc.value.message

    def test_resume_rejects_changed_kernel(self, store, settings):
        job = make_job(store, settings)
        with pytest.raises(InjectedInterrupt):
            job.run(fail_after=1)
        # tamper with the kernel spec inside the job state
        state = json.loads(job.state_path.read_text())
        state["spec"]["kernel_spec"]["weights"][0][0] += 0.5
        job.state_path.write_text(json.dumps(state))
        with pytest.raises(DigestMismatchError) as exc:
            TiledJob(job.state_path, store).resume()
        assert "kernel" in exc.value.message

    def test_resume_completed_job_rejected(self, store, settings):
        job = make_job(store, settings)
        job.run()
        with pytest.raises(JobStateError):
            job.resume()


class TestRunLog:
    def test_log_links_run_identity_and_progress(self, store, settings):
        job = make_job(store, settings)
        run_id = job.state["run_id"]
        job.run(fail_after=None)
        log_path = settings.logs_dir / f"run-{run_id}.jsonl"
        lines = [json.loads(x) for x in log_path.read_text().splitlines()]
        assert lines, "log file must not be empty"
        assert all(rec["run_id"] == run_id for rec in lines)
        events = [rec["event"] for rec in lines]
        assert "job_created" in events
        assert "digests_verified" in events
        assert "tile_done" in events
        assert "job_completed" in events
        created = next(r for r in lines if r["event"] == "job_created")
        assert "versions" in created and "numpy" in created["versions"]
        assert created["image_digest"] == job.state["spec"]["image_digest"]
        tile_events = [r for r in lines if r["event"] == "tile_done"]
        assert tile_events[-1]["progress"]["done"] == tile_events[-1]["progress"]["total"]
