"""HTTP API tests: envelope, error taxonomy, replay/verify endpoint.

Uses an in-memory store per app so test runs never touch a real SQLite file.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aipw.api import create_app
from aipw.simulation import KNOWN_TAU, make_clustered_dataset, make_dataset


@pytest.fixture
def client():
    app = create_app(":memory:")
    with TestClient(app) as c:
        yield c


def _body(dgp, **req_overrides):
    ds = dgp.dataset
    body = {
        "data": {"x": ds.x.tolist(), "a": ds.a.tolist(), "y": ds.y.tolist()},
        "seed": 0,
    }
    body.update(req_overrides)
    return body


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "ok"


def test_estimate_success_envelope_and_persistence(client):
    dgp = make_dataset(seed=1, n=1000)
    r = client.post("/estimate", json=_body(dgp, run_id="r1"))
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["success"] is True
    assert payload["run_id"] == "r1"
    data = payload["data"]
    assert data["run_id"] == "r1"
    assert abs(data["point"] - KNOWN_TAU) < 0.1
    assert data["independent_units"] == 1000
    assert data["folds"] == 5

    got = client.get("/runs/r1").json()["data"]
    assert got["state"] == "succeeded"
    assert got["result"]["point"] == data["point"]


def test_input_error_is_400_and_logged(client):
    dgp = make_dataset(seed=1, n=100)
    body = _body(dgp, run_id="bad-input")
    body["data"]["a"][0] = 3.0
    r = client.post("/estimate", json=body)
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["category"] == "input_error"
    assert "0 and 1" in err["message"]
    events = client.get("/runs/bad-input/events").json()["data"]["events"]
    assert events[-1]["state"] == "failed"
    assert events[-1]["category"] == "input_error"


def test_state_conflict_is_409_on_run_id_reuse(client):
    dgp = make_dataset(seed=1, n=200)
    body = _body(dgp, run_id="dup")
    assert client.post("/estimate", json=body).status_code == 200
    r2 = client.post("/estimate", json=body)
    assert r2.status_code == 409
    assert r2.json()["error"]["category"] == "state_conflict"


def test_resource_exhausted_is_507(client, monkeypatch):
    dgp = make_dataset(seed=1, n=100)
    with TestClient(create_app(":memory:")) as app_client:
        app_client.app.state.max_rows = 10
        r = app_client.post("/estimate", json=_body(dgp, run_id="too-big"))
        assert r.status_code == 507
        assert r.json()["error"]["category"] == "resource_exhausted"
        assert r.json()["error"]["details"]["budget"] == 10


def test_computation_failure_is_422_extreme_positivity(client):
    # Perfectly deterministic treatment as a function of x -> separation in
    # the logistic propensity fit -> computation_failed, never a 500.
    rng = np.random.default_rng(0)
    n = 400
    x = rng.normal(size=(n, 2))
    a = (x[:, 0] > 0).astype(float)
    y = rng.normal(size=n) + a
    body = {"data": {"x": x.tolist(), "a": a.tolist(), "y": y.tolist()},
            "seed": 0, "run_id": "separation"}
    r = client.post("/estimate", json=body)
    assert r.status_code == 422, r.text
    assert r.json()["error"]["category"] == "computation_failed"
    assert r.json()["run_id"] if "run_id" in r.json() else True


def test_cluster_ids_auto_enable_cluster_inference(client):
    dgp = make_clustered_dataset(seed=0, n_clusters=40, members_per_cluster=8)
    ds = dgp.dataset
    body = {"data": {"x": ds.x.tolist(), "a": ds.a.tolist(), "y": ds.y.tolist(),
                     "cluster_id": ds.clusters.tolist()},
            "seed": 0, "run_id": "cl"}
    r = client.post("/estimate", json=body)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["clustered"] is True
    assert data["independent_units"] == dgp.n_clusters


def test_verify_endpoint_reproduces_and_audits(client):
    dgp = make_dataset(seed=7, n=1500)
    body = _body(dgp, run_id="verify-me", seed=123)
    assert client.post("/estimate", json=body).status_code == 200
    r = client.post("/runs/verify-me/verify", json={"data": body["data"]})
    assert r.status_code == 200, r.text
    report = r.json()["data"]
    assert report["reproduced"] is True
    assert report["replay_point_diff"] < 1e-10
    assert report["oof_refit"]["passed"] is True
    assert report["leakage_audit"]["passed"] is True


def test_verify_rejects_run_that_does_not_exist(client):
    dgp = make_dataset(seed=1, n=100)
    r = client.post("/runs/ghost/verify", json={"data": _body(dgp)["data"]})
    assert r.status_code == 409


def test_verify_rejects_a_failed_run(client):
    dgp = make_dataset(seed=1, n=100)
    body = _body(dgp, run_id="failed-run")
    body["data"]["a"][0] = 5.0
    assert client.post("/estimate", json=body).status_code == 400
    body["data"]["a"][0] = 0.0
    r = client.post("/runs/failed-run/verify", json={"data": body["data"]})
    assert r.status_code == 400
    assert "succeeded run" in r.json()["error"]["message"]


def test_bad_run_id_pattern_rejected_by_framework(client):
    dgp = make_dataset(seed=1, n=100)
    body = {"data": {"x": dgp.dataset.x.tolist(), "a": dgp.dataset.a.tolist(),
                     "y": dgp.dataset.y.tolist()},
            "run_id": "bad id with spaces"}
    r = client.post("/estimate", json=body)
    assert r.status_code == 422


def test_events_carry_replay_fields(client):
    dgp = make_dataset(seed=2, n=500)
    client.post("/estimate", json=_body(dgp, run_id="ev", seed=4))
    events = client.get("/runs/ev/events").json()["data"]["events"]
    states = [e["state"] for e in events]
    assert states == ["pending", "running", "succeeded"]
    running = events[1]["detail"]
    assert set(["n", "p", "folds", "estimand", "clustered"]) <= set(running)
    success = events[2]["detail"]
    assert set(["point", "se", "ci", "fold_sizes", "components"]) <= set(success)
