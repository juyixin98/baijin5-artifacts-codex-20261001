"""Chunked job tests: progress, success, and explicit failure states."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings
from tests.conftest import fixture_b64


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings().validate()))


def _wait_for_terminal(client: TestClient, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        if data["status"] in ("succeeded", "failed"):
            return data
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


def test_job_succeeds_with_chunked_progress(client):
    resp = client.post(
        "/v1/jobs",
        json={
            "image_b64": fixture_b64("step_5x6.png"),
            "num_seams": 3,
            "chunk_size": 1,
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["data"]["job_id"]
    final = _wait_for_terminal(client, job_id)
    assert final["status"] == "succeeded"
    assert final["progress"] == {"done": 3, "total": 3}
    assert final["error"] is None
    paths = [s["path_original_cols"] for s in final["result"]["seams"]]
    assert paths == [[0, 0, 0, 0, 0], [1, 1, 1, 1, 1], [4, 4, 4, 4, 4]]
    assert final["result"]["final_width"] == 3


def test_job_fails_explicitly_when_no_legal_seam(client):
    resp = client.post(
        "/v1/jobs",
        json={
            "image_b64": fixture_b64("step_5x6.png"),
            "protect_mask_b64": fixture_b64("mask_row_5x6.png"),
            "num_seams": 2,
        },
    )
    assert resp.status_code == 202
    final = _wait_for_terminal(client, resp.json()["data"]["job_id"])
    # Failure must be explicit — never reported as success.
    assert final["status"] == "failed"
    assert final["error"]["category"] == "NO_LEGAL_SEAM"
    assert final["result"] is None
    assert final["progress"]["done"] == 0


def test_unknown_job_id_returns_404(client):
    resp = client.get("/v1/jobs/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "JOB_NOT_FOUND"


def test_job_invalid_num_seams_rejected_upfront(client):
    resp = client.post(
        "/v1/jobs",
        json={"image_b64": fixture_b64("spike_4x3.png"), "num_seams": 5},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "INVALID_REQUEST"
