"""HTTP integration tests: endpoints, error categories, job lifecycle, logs."""

from __future__ import annotations

import logging
import time

import pytest
from fastapi.testclient import TestClient

from graphcut import AppConfig
from graphcut.api import create_app

EXPLICIT_SPEC = {
    "width": 2,
    "height": 1,
    "unary": {
        "mode": "explicit",
        "cost0": [[1.0, 2.0]],
        "cost1": [[3.0, 0.5]],
    },
    "pairwise": {
        "mode": "explicit",
        "edges": [{"p": 0, "q": 1, "v00": 0.0, "v01": 1.0,
                   "v10": 1.0, "v11": 0.0}],
    },
}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(AppConfig(scipy_cross_check=True)))


def wait_for_job(client: TestClient, job_id: str, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        record = client.get(f"/v1/jobs/{job_id}").json()
        if record["state"] in {"succeeded", "failed", "cancelled"}:
            return record
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s")


class TestSegment:
    def test_happy_path_energy_decomposition(self, client):
        response = client.post("/v1/segment", json=EXPLICIT_SPEC)
        assert response.status_code == 200
        body = response.json()
        result = body["result"]
        energy = result["energy"]
        # hand-computed optimum for this spec: labeling (0,1), energy 2.5
        assert result["labeling"] == [[0, 1]]
        assert energy["data"] == pytest.approx(1.5)
        assert energy["smoothness"] == pytest.approx(1.0)
        assert energy["total"] == pytest.approx(2.5)
        assert energy["total"] == pytest.approx(
            energy["data"] + energy["smoothness"]
        )
        cert = result["certificate"]
        assert cert["consistent"] is True
        assert cert["flow_value"] == pytest.approx(cert["cut_capacity"])
        assert energy["total"] == pytest.approx(
            cert["graph_constant"] + cert["flow_value"]
        )
        assert cert["scipy_flow_value"] is not None
        assert body["run_id"]

    def test_non_submodular_rejected(self, client):
        payload = dict(EXPLICIT_SPEC)
        payload["pairwise"] = {
            "mode": "explicit",
            "edges": [{"p": 0, "q": 1, "v00": 1.0, "v01": 0.1,
                       "v10": 0.1, "v11": 1.0}],
        }
        response = client.post("/v1/segment", json=payload)
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["category"] == "input"
        assert error["code"] == "non_submodular_pairwise"

    def test_negative_data_term_rejected(self, client):
        payload = dict(EXPLICIT_SPEC)
        payload["unary"] = {
            "mode": "explicit",
            "cost0": [[-1.0, 2.0]],
            "cost1": [[3.0, 0.5]],
        }
        response = client.post("/v1/segment", json=payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "negative_data_term"

    def test_conflicting_seeds_rejected(self, client):
        payload = dict(EXPLICIT_SPEC)
        payload["seeds"] = {"foreground": [0, 1], "background": [1]}
        response = client.post("/v1/segment", json=payload)
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["category"] == "state_conflict"
        assert error["code"] == "conflicting_seeds"
        assert error["details"]["pixels"] == [1]

    def test_resource_limit_rejected(self):
        app = create_app(AppConfig(max_pixels=1))
        client = TestClient(app)
        response = client.post("/v1/segment", json=EXPLICIT_SPEC)
        assert response.status_code == 413
        error = response.json()["error"]
        assert error["category"] == "resource_exhausted"
        assert error["code"] == "too_many_pixels"


class TestJobs:
    def test_job_lifecycle(self, client):
        submitted = client.post("/v1/jobs", json=EXPLICIT_SPEC)
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        record = wait_for_job(client, job_id)
        assert record["state"] == "succeeded"
        assert record["result"]["energy"]["total"] == pytest.approx(2.5)
        assert record["result"]["run_id"] == job_id  # job id == log run id

    def test_cancel_finished_job_is_state_conflict(self, client):
        job_id = client.post("/v1/jobs", json=EXPLICIT_SPEC).json()["job_id"]
        wait_for_job(client, job_id)
        response = client.post(f"/v1/jobs/{job_id}/cancel")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "job_already_finished"

    def test_unknown_job(self, client):
        response = client.get("/v1/jobs/doesnotexist")
        assert response.status_code == 404
        assert response.json()["error"]["category"] == "input"

    def test_failed_job_reports_computation_category(
        self, client, monkeypatch
    ):
        from graphcut import pipeline

        def boom(*args, **kwargs):
            raise RuntimeError("solver exploded")

        monkeypatch.setattr(pipeline, "solve_maxflow", boom)
        job_id = client.post("/v1/jobs", json=EXPLICIT_SPEC).json()["job_id"]
        record = wait_for_job(client, job_id)
        assert record["state"] == "failed"
        assert record["error"]["category"] == "computation"
        assert record["error"]["code"] == "unexpected_error"


class TestValidate:
    def test_correct_claim_matches(self, client):
        response = client.post("/v1/validate", json={
            "spec": EXPLICIT_SPEC,
            "labeling": [[0, 1]],
            "claimed_energy": 2.5,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["energy"]["total"] == pytest.approx(2.5)
        assert body["matches_claim"] is True

    def test_wrong_claim_flagged(self, client):
        response = client.post("/v1/validate", json={
            "spec": EXPLICIT_SPEC,
            "labeling": [[1, 1]],
            "claimed_energy": 2.5,
        })
        body = response.json()
        assert body["energy"]["total"] == pytest.approx(3.5)
        assert body["matches_claim"] is False

    def test_bad_labeling_rejected(self, client):
        response = client.post("/v1/validate", json={
            "spec": EXPLICIT_SPEC,
            "labeling": [[0, 1, 0]],
        })
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "bad_labeling_shape"


class TestRunLogs:
    def test_run_id_and_intermediate_states_logged(self, client, caplog):
        with caplog.at_level(logging.INFO, logger="graphcut"):
            response = client.post("/v1/segment", json=EXPLICIT_SPEC)
        run_id = response.json()["run_id"]
        text = caplog.text
        assert f"run_id={run_id}" in text
        for event in ("run_started", "graph_built", "maxflow_done",
                      "certificate_ok", "run_finished"):
            assert f"event={event}" in text
        # key intermediate state is present for replay
        assert "flow=" in text and "energy=" in text

    def test_rejection_logs_reason(self, client, caplog):
        payload = dict(EXPLICIT_SPEC)
        payload["seeds"] = {"foreground": [0], "background": [0]}
        with caplog.at_level(logging.INFO, logger="graphcut"):
            client.post("/v1/segment", json=payload)
        assert "event=request_rejected" in caplog.text
        assert "conflicting_seeds" in caplog.text
