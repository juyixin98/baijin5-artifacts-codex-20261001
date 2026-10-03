"""End-to-end HTTP tests against the FastAPI app (in-process TestClient)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from miniseed.api import create_app
from miniseed.config import Settings


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = Settings(
        k=9,
        w=5,
        max_bucket_size=200,
        max_candidates=500,
        db_path=tmp_path / "api.db",
    )
    app = create_app(settings)
    # raise_server_exceptions=False so the production 500 handler is exercised
    # instead of TestClient re-raising.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_health_reports_versions(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "miniseed"
    assert body["hash_version"] == "fnv1a-2bit-v1"
    assert "python" in body and "numpy" in body


def test_index_and_query_roundtrip_with_correlation_id(client):
    ref = "CAGTACCTGAGATCGATCGTTACCGGTAATTTTTTTTTTTTTTTTTTTTTTGGCTATCGGATCCAAGTCACTGAGTCTGA"
    r = client.post(
        "/api/v1/runs",
        json={"run_id": "r1", "reference": ref},
        headers={"x-request-id": "fixed-id-001"},
    )
    assert r.status_code == 200, r.text
    assert r.headers["x-request-id"] == "fixed-id-001"
    idx = r.json()
    assert idx["request_id"] == "fixed-id-001"
    assert idx["seed_count"] > 0

    read = ref[:28]
    q = client.post(
        "/api/v1/query",
        json={"run_id": "r1", "read": read},
        headers={"x-request-id": "fixed-id-001"},
    )
    assert q.status_code == 200, q.text
    qb = q.json()
    assert qb["total_hits"] > 0
    assert qb["candidate_is_alignment"] is False
    assert qb["locations"][0]["hit_count"] >= 1

    audit = client.get("/api/v1/runs/r1/audit").json()
    events = audit["events"]
    assert any(e["identity"] == "fixed-id-001" for e in events)


def test_error_envelope_uses_stable_category(client):
    # Invalid character -> 400 with INVALID_CHARACTER, ok is explicitly false.
    r = client.post(
        "/api/v1/runs", json={"run_id": "bad", "reference": "ACGTXACGT"}
    )
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_CHARACTER"
    assert body["error"]["context"]["character"] == "X"


def test_parameter_conflict_over_http(client):
    ref = "A" * 40 + "C" * 40
    client.post("/api/v1/runs", json={"run_id": "r9", "reference": ref})
    r = client.post(
        "/api/v1/query",
        json={"run_id": "r9", "read": "A" * 20, "k": 7, "w": 5},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "PARAMETER_CONFLICT"


def test_unknown_run_is_404(client):
    r = client.post(
        "/api/v1/query",
        json={"run_id": "missing", "read": "ACGTACGTACGTAC"},
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_schema_validation_error_is_categorized(client):
    r = client.post("/api/v1/runs", json={"run_id": "", "reference": "ACGT"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_PARAMETER"


def test_list_and_get_run_endpoints(client):
    ref = "ACGTACGTACGTACGTACGT" + "C" * 20
    client.post("/api/v1/runs", json={"run_id": "ls1", "reference": ref})
    listing = client.get("/api/v1/runs").json()
    assert any(r["run_id"] == "ls1" for r in listing["runs"])

    detail = client.get("/api/v1/runs/ls1").json()
    assert detail["run"]["run_id"] == "ls1"
    assert detail["bucket_count"] >= 1

    missing = client.get("/api/v1/runs/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_unexpected_exception_is_never_reported_as_success(client, monkeypatch):
    # Force an unexpected failure inside the service and confirm the API maps
    # it to INTERNAL_ERROR with ok=false rather than a fake success.
    def boom(*_a, **_k):
        raise RuntimeError("synthetic unexpected failure")

    monkeypatch.setattr(
        client.app.state.service, "index_reference", boom
    )
    r = client.post(
        "/api/v1/runs", json={"run_id": "x", "reference": "ACGTACGTACGTAC"}
    )
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INTERNAL_ERROR"
