"""HTTP boundary: success payload and the four distinct error statuses."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aipw_backend.api import create_app
from aipw_backend.dgp import generate_sample


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "api.db"))
    with TestClient(app) as c:
        yield c


def _payload(n=600, seed=0, tau=2.0, cluster=False):
    s = generate_sample(
        n, seed=seed, tau=tau,
        n_clusters=60 if cluster else None,
        cluster_size=10 if cluster else None,
        icc=0.5 if cluster else 0.0,
    )
    body = {
        "x": s.x.tolist(),
        "a": s.a.astype(int).tolist(),
        "y": s.y.tolist(),
        "known_effect": tau,
    }
    if cluster:
        body["cluster"] = s.cluster.tolist()
    return body


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_run_returns_estimate_and_evidence(client):
    r = client.post("/api/runs", json=_payload())
    assert r.status_code == 201, r.text
    ev = r.json()
    assert abs(ev["estimate"] - 2.0) < 0.2
    assert ev["ci"][0] < 2.0 < ev["ci"][1]
    assert ev["ci_covers_known"] is True
    rid = ev["run_id"]
    fetched = client.get(f"/api/runs/{rid}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"


def test_cluster_payload_reports_cluster_units(client):
    r = client.post("/api/runs", json=_payload(cluster=True))
    assert r.status_code == 201
    assert r.json()["independent_unit"] == "cluster"
    assert r.json()["n_clusters"] == 60


def test_input_error_is_400(client):
    body = _payload()
    body["a"] = body["a"][:-1]  # misaligned lengths
    r = client.post("/api/runs", json=body)
    assert r.status_code == 400
    assert r.json()["error"] == "input_error"


def test_resource_exhausted_is_413(tmp_path):
    import json

    cfg_path = tmp_path / "small.json"
    cfg_path.write_text(
        json.dumps(
            {
                "folds": {"n_splits": 5, "seed": 1, "stratified": True},
                "max_feature_cells": 100,
            }
        )
    )
    app = create_app(db_path=str(tmp_path / "api413.db"), config_path=str(cfg_path))
    with TestClient(app) as c:
        body = _payload()
        # Default payload is 600 x 2 = 1200 cells > 100 budget.
        r = c.post("/api/runs", json=body)
    assert r.status_code == 413
    assert r.json()["error"] == "resource_exhausted"
    assert r.json()["details"]["budget"] == 100


def test_state_conflict_on_run_id_reuse_is_409(client):
    body = _payload(n=300)
    body["run_id"] = "dup"
    assert client.post("/api/runs", json=body).status_code == 201
    r = client.post("/api/runs", json=body)
    assert r.status_code == 409
    assert r.json()["error"] == "state_conflict"


def test_computation_failure_is_422(client):
    rng = np.random.default_rng(0)
    n = 80
    x = rng.standard_normal((n, 1)) * 20
    a = (x[:, 0] > 0).astype(int)
    y = rng.standard_normal(n)
    r = client.post(
        "/api/runs", json={"x": x.tolist(), "a": a.tolist(), "y": y.tolist()}
    )
    assert r.status_code == 422
    assert r.json()["error"] == "computation_failure"


def test_unknown_run_is_404(client):
    r = client.get("/api/runs/nope")
    assert r.status_code == 404
    assert r.json()["error"] == "state_conflict"
