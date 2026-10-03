"""HTTP verification interface: concrete results, error semantics, TSV flow."""

import pytest
from fastapi.testclient import TestClient

from depthcov.api import create_app
from depthcov.config import Settings
from depthcov.synthetic import TINY_EXPECTED_DEPTH, tiny_alignments


@pytest.fixture
def client():
    with TestClient(create_app(Settings(db_path=":memory:"))) as c:
        yield c


def _tiny_payload(**opts):
    return {
        "references": [{"name": "chrTiny", "length": 10}],
        "alignments": [
            {
                "query_name": a.query_name,
                "ref_name": a.ref_name,
                "ref_start": a.ref_start,
                "cigar": a.cigar,
                "mapq": a.mapq,
                "is_duplicate": a.is_duplicate,
            }
            for a in tiny_alignments()
        ],
        "options": {"min_mapq": 20, **opts},
    }


def test_healthz(client):
    r = client.get("/healthz", headers={"x-request-id": "req-xyz"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["request_id"] == "req-xyz"
    assert r.headers["x-request-id"] == "req-xyz"


def test_analyze_returns_exact_per_base_depth(client):
    r = client.post("/api/v1/analyze", json=_tiny_payload())
    assert r.status_code == 200
    body = r.json()
    result = body["results"]["chrTiny"]
    assert result["per_base_depth"] == TINY_EXPECTED_DEPTH
    assert result["weighted_length"] == sum(TINY_EXPECTED_DEPTH)
    assert body["counts"] == {
        "input": 7, "accepted": 3, "rejected": 4, "undetermined": 0,
    }


def test_diagnostics_classify_each_record(client):
    body = client.post("/api/v1/analyze", json=_tiny_payload()).json()
    by_id = {d["record_id"]: d for d in body["diagnostics"]}
    assert by_id["r6"]["outcome"] == "rejected"
    assert by_id["r6"]["reason"] == "low_mapq"
    assert by_id["r7"]["reason"] == "out_of_bounds"
    assert by_id["r5"]["reason"] == "duplicate"
    assert by_id["r1"]["outcome"] == "accepted"
    # Every diagnostic carries the same request correlation id.
    assert len({d["request_id"] for d in body["diagnostics"]}) == 1


def test_bad_cigar_is_a_rejected_record_not_http_error(client):
    payload = _tiny_payload()
    payload["alignments"].append(
        {"query_name": "weird", "ref_name": "chrTiny", "ref_start": 0,
         "cigar": "9Q", "mapq": 60}
    )
    body = client.post("/api/v1/analyze", json=payload).json()
    assert body["counts"]["rejected"] == 5
    diag = next(d for d in body["diagnostics"] if d["record_id"] == "weird")
    assert diag["reason"] == "invalid_cigar"


def test_schema_violation_is_http_422(client):
    r = client.post("/api/v1/analyze", json={"references": []})
    assert r.status_code == 422


def test_duplicate_reference_names_is_http_400(client):
    payload = {
        "references": [{"name": "x", "length": 5},
                       {"name": "x", "length": 6}],
        "alignments": [],
    }
    r = client.post("/api/v1/analyze", json=payload)
    assert r.status_code == 400


def test_per_record_policy_changes_depth(client):
    payload = _tiny_payload(dedup_policy="per_record")
    # r3 appears twice -> double counted under per_record.
    body = client.post("/api/v1/analyze", json=payload).json()
    assert body["results"]["chrTiny"]["per_base_depth"][3:7] == [3, 3, 3, 3]


def test_tsv_malformed_row_is_undetermined(client):
    tsv = (
        "good\tchrTiny\t0\t60\t\t+\td\t0\t4M\n"
        "BROKENROW\n"
        "badnum\tchrTiny\tXX\t60\t\t+\td\t0\t2M\n"
    )
    r = client.post(
        "/api/v1/analyze/tsv",
        json={
            "references": [{"name": "chrTiny", "length": 10}],
            "tsv": tsv,
            "options": {"use_external_sort": False},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == {
        "input": 3, "accepted": 1, "rejected": 0, "undetermined": 2,
    }
    reasons = {d["record_id"]: d["reason"] for d in body["diagnostics"]}
    assert reasons["row#2"] == "malformed_record"
    assert reasons["row#3"] == "malformed_record"
    # Raw bad content must not be echoed verbatim.
    assert "BROKENROW" not in str(body["diagnostics"])


def test_run_readback_endpoint(client):
    body = client.post("/api/v1/analyze", json=_tiny_payload()).json()
    run_id = body["run_id"]
    got = client.get(f"/api/v1/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["accepted_count"] == 3
    assert client.get("/api/v1/runs/nope").status_code == 404


def test_gap_carrying_read_segments(client):
    payload = {
        "references": [{"name": "chrTiny", "length": 10}],
        "alignments": [
            {"query_name": "g", "ref_name": "chrTiny", "ref_start": 1,
             "cigar": "3M2D2M", "mapq": 60}
        ],
    }
    result = client.post("/api/v1/analyze", json=payload).json()["results"]["chrTiny"]
    assert [(s["start"], s["end"], s["depth"]) for s in result["segments"]] == [
        (1, 4, 1), (6, 8, 1)
    ]
    assert result["weighted_length"] == 5
