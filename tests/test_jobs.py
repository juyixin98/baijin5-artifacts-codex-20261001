"""Chunked job tests: lifecycle, progress, failure categories, log identity."""

from __future__ import annotations

import time

import numpy as np

from app.config import Settings
from app.jobs.manager import JobManager, JobStatus
from tests import fixtures
from tests.conftest import events


def _wait_for_terminal(client, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        view = client.get(f"/v1/jobs/{job_id}").json()
        if view["status"] in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value):
            return view
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_job_lifecycle_matches_sync_result(client):
    elevation, markers = fixtures.noisy_gradient()
    payload = {
        "elevation": elevation.tolist(),
        "markers": markers.tolist(),
        "connectivity": 8,
        "chunk_size": 16,
    }
    submitted = client.post("/v1/jobs", json=payload)
    assert submitted.status_code == 202
    job_id = submitted.json()["job_id"]

    final = _wait_for_terminal(client, job_id)
    assert final["status"] == "succeeded"
    assert final["progress"] == 1.0
    assert final["chunks_done"] >= 1
    assert final["pixels_processed"] == final["pixels_total"] == elevation.size
    assert final["finished_at"] is not None

    result = client.get(f"/v1/jobs/{job_id}/result")
    assert result.status_code == 200
    body = result.json()
    sync = client.post("/v1/segment", json={k: v for k, v in payload.items() if k != "chunk_size"})
    assert body["labels"] == sync.json()["labels"], "chunked job must equal the synchronous run"
    assert body["boundary"] == sync.json()["boundary"]
    assert body["run_id"] == final["run_id"]
    assert body["input_sha256"] == final["input_sha256"]


def test_job_progress_events_carry_job_identity(client, log_capture):
    elevation, markers = fixtures.noisy_gradient()
    payload = {"elevation": elevation.tolist(), "markers": markers.tolist(), "chunk_size": 8}
    job_id = client.post("/v1/jobs", json=payload).json()["job_id"]
    _wait_for_terminal(client, job_id)

    chunk_events = [f for f in events(log_capture, "job_chunk_done") if f.get("job_id") == job_id]
    assert chunk_events, "no chunk progress events logged for the job"
    assert all(f["run_id"] for f in chunk_events)
    assert chunk_events[-1]["pixels_processed"] == chunk_events[-1]["pixels_total"]
    succeeded = events(log_capture, "job_succeeded")
    assert any(f.get("job_id") == job_id for f in succeeded)


def test_unknown_job_returns_404(client):
    response = client.get("/v1/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["detail"]["category"] == "job_not_found"
    assert client.get("/v1/jobs/does-not-exist/result").status_code == 404


def test_failed_job_reports_category_not_success(settings):
    """A kernel-level contract violation fails the job with its category."""
    manager = JobManager(settings)
    record = manager.submit(
        np.ones((3, 3)), np.zeros((3, 3), dtype=int),  # no seeds
        connectivity=8, mask=None, chunk_size=2, input_sha256="0" * 64,
    )
    deadline = time.monotonic() + 5.0
    while record.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        assert time.monotonic() < deadline, "job did not finish"
        time.sleep(0.02)
    assert record.status is JobStatus.FAILED
    assert record.error_category == "no_seeds"
    assert record.error_message
    assert record.labels is None


def test_failed_job_result_endpoint_returns_error(client, settings):
    manager: JobManager = client.app.state.job_manager
    record = manager.submit(
        np.ones((2, 2)), np.zeros((2, 2), dtype=int),
        connectivity=8, mask=None, chunk_size=1, input_sha256="f" * 64,
    )
    deadline = time.monotonic() + 5.0
    while record.status not in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    view = client.get(f"/v1/jobs/{record.job_id}").json()
    assert view["status"] == "failed"
    assert view["error"]["category"] == "no_seeds"
    result = client.get(f"/v1/jobs/{record.job_id}/result")
    assert result.status_code == 422
    assert result.json()["detail"]["category"] == "no_seeds"


def test_job_submit_validates_contract_first(client):
    payload = {"elevation": [[1.0, 1.0]], "markers": [[0, 0]]}
    response = client.post("/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "no_seeds"
