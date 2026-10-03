"""API tests: contract shapes, failure categories, request-id propagation."""

import numpy as np
from fastapi.testclient import TestClient

from dtw_service.api import create_app

client = TestClient(create_app())


def _payload(query, reference, **extra):
    return {"query": query, "reference": reference, **extra}


def test_health():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["normalization"] == "path_length"


def test_align_ok_returns_path_cost_and_stretch():
    query = np.sin(0.3 * np.arange(20)).reshape(-1, 1).tolist()
    reference = np.sin(0.3 * np.arange(24)).reshape(-1, 1).tolist()
    resp = client.post("/v1/dtw/align", json=_payload(query, reference, window=8))
    assert resp.status_code == 200
    body = resp.json()

    assert body["status"] == "ok"
    assert body["request_id"]
    assert resp.headers["x-request-id"] == body["request_id"]
    assert body["path"][0] == [0, 0]
    assert body["path"][-1] == [19, 23]
    assert body["path_length"] == len(body["path"])
    # Fixed denominator convention, checkable by the client.
    assert body["normalized_cost"] == body["total_cost"] / body["path_length"]
    assert len(body["stretch"]) == body["path_length"]
    # Path monotonicity verified client-side too.
    steps = np.diff(np.array(body["path"]), axis=0)
    assert (steps >= 0).all() and (steps.sum(axis=1) >= 1).all()
    # Diagnostics carry masked inputs only: shapes and hashes, no raw values.
    assert body["diagnostics"]["inputs"]["query"]["shape"] == [20, 1]
    assert "sha256_12" in body["diagnostics"]["inputs"]["query"]


def test_client_supplied_request_id_is_echoed():
    resp = client.post(
        "/v1/dtw/align",
        json=_payload([[0.0], [1.0]], [[0.0], [1.0]], request_id="req-test-1"),
    )
    assert resp.status_code == 200
    assert resp.json()["request_id"] == "req-test-1"
    assert resp.headers["x-request-id"] == "req-test-1"


def test_unreachable_is_structured_failure_not_500():
    query = [[float(t)] for t in range(10)]
    reference = [[float(t)] for t in range(30)]
    resp = client.post("/v1/dtw/align", json=_payload(query, reference, window=5))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "unreachable"
    assert "Sakoe-Chiba" in body["reason"]
    assert body["path"] is None
    assert body["normalized_cost"] is None


def test_empty_sequence_is_rejected_as_422():
    resp = client.post("/v1/dtw/align", json=_payload([], [[1.0]]))
    assert resp.status_code == 422


def test_mismatched_dimensions_are_rejected_as_422():
    resp = client.post("/v1/dtw/align", json=_payload([[1.0, 2.0]], [[1.0]]))
    assert resp.status_code == 422


def test_non_finite_values_are_rejected_as_422():
    import json

    payload = json.dumps(_payload([[float("nan")]], [[1.0]]))
    resp = client.post(
        "/v1/dtw/align", content=payload, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 422
