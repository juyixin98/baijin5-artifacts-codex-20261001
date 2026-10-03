"""API tests: HTTP semantics, provenance retrieval, error categories."""

import pytest
from fastapi.testclient import TestClient

from coverage_depth.api import create_app

from conftest import EXPECTED_HISTOGRAM, EXPECTED_SEGMENTS, REF_LENGTH, REF_NAME


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "prov.db"))
    with TestClient(app) as c:
        yield c


def fixture_body():
    return {
        "reference": {"name": REF_NAME, "length": REF_LENGTH},
        "alignments": [
            {"read_id": "r1", "start": 5, "mapq": 30, "flags": 0, "cigar": "5M5N5M"},
            {"read_id": "r2", "start": 8, "mapq": 30, "flags": 0, "cigar": "10M"},
            {"read_id": "r3", "start": 20, "mapq": 30, "flags": 0, "cigar": "10M"},
            {"read_id": "r3", "start": 25, "mapq": 30, "flags": 0, "cigar": "10M"},
            {"read_id": "r4", "start": 0, "mapq": 60, "flags": 1024, "cigar": "40M"},
            {"read_id": "r5", "start": 0, "mapq": 10, "flags": 0, "cigar": "10M"},
            {"read_id": "r6", "start": 0, "mapq": None, "flags": 0, "cigar": "10M"},
            {"read_id": "r7", "start": 35, "mapq": 30, "flags": 0, "cigar": "10M"},
        ],
    }


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_run_and_fetch_provenance(client):
    resp = client.post("/v1/coverage/runs", json=fixture_body())
    assert resp.status_code == 201
    assert "X-Request-Id" in resp.headers
    created = resp.json()
    run_id = created["run_id"]
    assert created["histogram"] == {str(k): v for k, v in EXPECTED_HISTOGRAM.items()}
    assert created["covered_bases"] == 30
    assert created["weighted_bases"] == 35
    assert created["input_sha256"]
    assert created["config"]["filter"]["min_mapq"] == 20
    assert created["counters"]["ACCEPTED:ACCEPTED"] == 4
    assert created["counters"]["REJECTED:DUPLICATE"] == 1
    assert created["counters"]["REJECTED:LOW_MAPQ"] == 1
    assert created["counters"]["REJECTED:OUT_OF_BOUNDS"] == 1
    assert created["counters"]["UNDECIDABLE:MAPQ_UNKNOWN"] == 1

    fetched = client.get(f"/v1/coverage/runs/{run_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert [
        (s["start"], s["end"], s["depth"]) for s in body["segments"]
    ] == EXPECTED_SEGMENTS


def test_decisions_endpoint_and_status_filter(client):
    run_id = client.post("/v1/coverage/runs", json=fixture_body()).json()["run_id"]

    rejected = client.get(f"/v1/coverage/runs/{run_id}/decisions?status=REJECTED")
    assert rejected.status_code == 200
    reasons = {d["reason"] for d in rejected.json()["decisions"]}
    assert reasons == {"DUPLICATE", "LOW_MAPQ", "OUT_OF_BOUNDS"}
    # Read names are stored only as digests.
    for d in rejected.json()["decisions"]:
        assert set(d) == {"record_index", "read_id_digest", "status", "reason", "detail"}
        assert len(d["read_id_digest"]) == 16

    undecidable = client.get(f"/v1/coverage/runs/{run_id}/decisions?status=UNDECIDABLE")
    assert [d["reason"] for d in undecidable.json()["decisions"]] == ["MAPQ_UNKNOWN"]

    bad_filter = client.get(f"/v1/coverage/runs/{run_id}/decisions?status=MAYBE")
    assert bad_filter.status_code == 400
    assert bad_filter.json()["error"] == "BadStatusFilter"


def test_unknown_run_is_404(client):
    resp = client.get("/v1/coverage/runs/nope")
    assert resp.status_code == 404
    assert resp.json()["error"] == "RunNotFound"
    resp = client.get("/v1/coverage/runs/nope/decisions")
    assert resp.status_code == 404


def test_empty_alignments_rejected_by_schema(client):
    body = fixture_body()
    body["alignments"] = []
    resp = client.post("/v1/coverage/runs", json=body)
    assert resp.status_code == 422


def test_invalid_reference_rejected_by_schema(client):
    body = fixture_body()
    body["reference"]["length"] = 0
    resp = client.post("/v1/coverage/runs", json=body)
    assert resp.status_code == 422


def test_record_level_errors_do_not_fail_the_run(client):
    # r7 is out of bounds and r6 undecidable, yet the run completes 201.
    resp = client.post("/v1/coverage/runs", json=fixture_body())
    assert resp.status_code == 201
