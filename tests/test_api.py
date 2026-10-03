"""HTTP surface tests via FastAPI TestClient."""

import pytest
from fastapi.testclient import TestClient

from dtw_service.api import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_dtw_success_concrete_values():
    res = client.post("/v1/dtw", json={
        "sequence_a": [0.0, 1.0],
        "sequence_b": [0.0, 2.0],
        "window_radius": 1,
        "record_id": "api-1",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted"
    assert body["request_id"] == "api-1"
    assert body["path"] == [[0, 0], [1, 1]]
    assert body["cost"] == pytest.approx(1.0)
    assert body["normalized_cost"] == pytest.approx(0.25)
    assert body["stretch"] == [1.0]
    assert body["failure"] is None


def test_dtw_empty_sequence_failure_category():
    res = client.post("/v1/dtw", json={"sequence_a": [], "sequence_b": [1.0]})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "rejected"
    assert body["failure"]["category"] == "empty_sequence"


def test_dtw_too_narrow_window_failure_category():
    res = client.post("/v1/dtw", json={
        "sequence_a": [0.0] * 10,
        "sequence_b": [0.0] * 4,
        "window_radius": 2,
    })
    body = res.json()
    assert body["status"] == "rejected"
    assert body["failure"]["category"] == "window_too_narrow"


def test_dtw_malformed_payload_is_422():
    res = client.post("/v1/dtw", json={"sequence_a": "nope", "sequence_b": [1.0]})
    assert res.status_code == 422


def test_dtw_unknown_field_is_422():
    res = client.post("/v1/dtw", json={
        "sequence_a": [1.0], "sequence_b": [1.0], "metric": "euclidean",
    })
    assert res.status_code == 422  # metric is contract-fixed, not settable
