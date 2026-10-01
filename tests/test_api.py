"""API integration tests using FastAPI's TestClient with a temp DB.

The store dependency is overridden to point at a temporary SQLite database,
and the override is cleared afterwards (dependency-injection contract).
"""
from __future__ import annotations

import numpy as np
import pytest

from fastapi.testclient import TestClient

from app.api import main as api_main
from app.dgp import sharp_jump, sparse_boundary
from app.storage import RunStore


@pytest.fixture
def client(tmp_path):
    store = RunStore(tmp_path / "test_rd.db")
    api_main._store = store
    with TestClient(api_main.app) as c:
        yield c
    api_main._store = None
    api_main.app.dependency_overrides.clear()


def _payload(d, **over):
    body = {"x": d.x.tolist(), "y": d.y.tolist(), "cutoff": d.cutoff}
    body.update(over)
    return body


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_estimate_success_roundtrip_and_persistence(client):
    d = sharp_jump(n=2000, tau=6.0, seed=1)
    r = client.post("/api/v1/rd/estimate",
                    json=_payload(d, bandwidth="ik", run_id="api-1"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert abs(body["estimate"]["tau"] - 6.0) < 0.6
    assert body["run_id"] == "api-1"
    assert body["versions"]["numpy"] == np.__version__

    # Persisted and retrievable by run id.
    got = client.get("/api/v1/runs/api-1")
    assert got.status_code == 200
    assert got.json()["estimate"]["tau"] == body["estimate"]["tau"]

    listed = client.get("/api/v1/runs").json()["runs"]
    assert any(row["run_id"] == "api-1" for row in listed)


def test_estimate_failed_returns_structured_422_not_blank_success(client):
    d = sparse_boundary(n=800, inner_gap=0.25, seed=2)
    r = client.post("/api/v1/rd/estimate",
                    json=_payload(d, bandwidth=0.1, run_id="api-fail"))
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "failed"
    assert body["failure_category"] == "non_identifiable"
    assert body["estimate"]["tau"] is None
    # Even a failed run is persisted for audit.
    assert client.get("/api/v1/runs/api-fail").status_code == 200


def test_validation_error_on_short_series(client):
    r = client.post("/api/v1/rd/estimate",
                    json={"x": [1.0, 2.0], "y": [1.0, 2.0]})
    assert r.status_code == 422


def test_validation_error_on_unequal_length(client):
    r = client.post("/api/v1/rd/estimate",
                    json={"x": [-1.0, 1.0, 0.5], "y": [1.0, 2.0]})
    assert r.status_code == 422


def test_unknown_run_is_404(client):
    assert client.get("/api/v1/runs/does-not-exist").status_code == 404
