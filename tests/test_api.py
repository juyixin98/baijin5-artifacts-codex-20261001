"""API-level tests: request identity, provenance round-trip, failure categories."""

import dataclasses
import hashlib
import json

from fastapi.testclient import TestClient

from app.main import create_app

from conftest import load_fixture


def _client(settings):
    return TestClient(create_app(settings))


def test_health_and_version(settings):
    client = _client(settings)
    assert client.get("/v1/health").json() == {"status": "up"}
    version = client.get("/v1/version").json()
    assert version["app_version"]
    assert version["algorithm_version"] == "mec-exact-1"


def test_phase_clean_fixture_and_provenance_roundtrip(settings):
    client = _client(settings)
    payload = load_fixture("clean_two_site.json")
    response = client.post("/v1/phase", json=payload)
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == "OK"
    assert body["request_id"].startswith("req_")
    assert body["versions"]["algorithm"] == "mec-exact-1"
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert body["input_sha256"] == expected_hash
    assert body["blocks"][0]["haplotypes"] == {"H1": ["A", "C"], "H2": ["G", "T"]}

    record = client.get(f"/v1/runs/{body['request_id']}")
    assert record.status_code == 200
    record_body = record.json()
    assert record_body["status"] == "OK"
    assert record_body["input_sha256"] == expected_hash
    assert record_body["failure_category"] is None
    assert record_body["result"]["blocks"][0]["mec_score"] == 0.0


def test_ambiguous_run_recorded_with_uncertainties(settings):
    client = _client(settings)
    body = client.post("/v1/phase", json=load_fixture("ambiguous.json")).json()
    assert body["status"] == "AMBIGUOUS"
    assert body["blocks"][0]["num_optimal_solutions"] == 2
    assert body["uncertainties"]
    record = client.get(f"/v1/runs/{body['request_id']}").json()
    assert record["status"] == "AMBIGUOUS"


def test_invalid_input_failure_category(settings):
    client = _client(settings)
    payload = {
        "variants": [{"id": "v1", "pos": 1, "ref": "A", "alt": "A"}],
        "reads": [],
    }
    body = client.post("/v1/phase", json=payload).json()
    assert body["status"] == "FAILED"
    assert body["failure"]["category"] == "INVALID_INPUT"
    # Failures are also traceable through provenance.
    record = client.get(f"/v1/runs/{body['request_id']}").json()
    assert record["failure_category"] == "INVALID_INPUT"


def test_no_observations_failure_category(settings):
    client = _client(settings)
    payload = {
        "variants": [{"id": "v1", "pos": 1, "ref": "A", "alt": "G"}],
        "reads": [],
    }
    body = client.post("/v1/phase", json=payload).json()
    assert body["failure"]["category"] == "NO_OBSERVATIONS"


def test_block_too_large_failure_category(settings):
    small = dataclasses.replace(settings, max_enum_sites=1)
    client = _client(small)
    payload = load_fixture("clean_two_site.json")
    body = client.post("/v1/phase", json=payload).json()
    assert body["status"] == "FAILED"
    assert body["failure"]["category"] == "BLOCK_TOO_LARGE"


def test_unknown_run_returns_404(settings):
    client = _client(settings)
    response = client.get("/v1/runs/req_doesnotexist")
    assert response.status_code == 404
    assert response.json()["failure"]["category"] == "RUN_NOT_FOUND"


def test_schema_validation_rejects_empty_variants(settings):
    client = _client(settings)
    response = client.post("/v1/phase", json={"variants": [], "reads": []})
    assert response.status_code == 422
