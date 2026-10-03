"""API integration tests via FastAPI TestClient."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.samples import fork, ring

client = TestClient(app)


def test_health() -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"]


def test_thin_ring_full_engine() -> None:
    response = client.post("/v1/thin", json={"sample": "ring"})
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "full"
    assert body["converged"] is True
    assert body["rounds"] > 1
    assert body["deletions_per_round"][-1] == [0, 0]
    assert body["request_id"]
    skeleton = np.asarray(body["skeleton"], dtype=np.uint8)
    assert 0 < skeleton.sum() < np.asarray(ring()).sum()


def test_tiled_engine_matches_full_engine_over_api() -> None:
    full = client.post("/v1/thin", json={"sample": "fork", "engine": "full"}).json()
    tiled = client.post(
        "/v1/thin", json={"sample": "fork", "engine": "tiled", "tile_size": 4}
    ).json()
    assert tiled["tile_count"] == 30  # 20x21 image, 4px tiles -> 5x6 grid
    assert tiled["skeleton"] == full["skeleton"]
    assert tiled["deletions_per_round"] == full["deletions_per_round"]


def test_graph_endpoint_on_fork() -> None:
    body = client.post("/v1/graph", json={"sample": "fork"}).json()
    summary = body["graph"]["summary"]
    assert summary["endpoint_count"] == 3
    assert summary["junction_count"] == 1
    assert summary["edge_count"] == 3
    for edge in body["graph"]["edges"]:
        assert len(edge["pixels"]) >= 2  # original pixel chain preserved


def test_validate_endpoint_on_ring() -> None:
    body = client.post("/v1/validate", json={"sample": "ring"}).json()
    report = body["report"]
    assert report["passed"] is True
    assert report["failures"] == []
    assert (report["holes_before"], report["holes_after"]) == (1, 1)


def test_request_id_header_is_echoed() -> None:
    response = client.post(
        "/v1/thin", json={"sample": "line"}, headers={"x-request-id": "trace-42"}
    )
    assert response.headers["x-request-id"] == "trace-42"
    assert response.json()["request_id"] == "trace-42"


def test_non_binary_pixels_rejected_with_category() -> None:
    response = client.post("/v1/thin", json={"pixels": [[0, 1], [1, 2]]})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["category"] == "contract_violation"
    assert "binary" in error["detail"]


def test_ragged_pixels_rejected() -> None:
    response = client.post("/v1/thin", json={"pixels": [[0, 1], [1]]})
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "contract_violation"


def test_ambiguous_source_rejected() -> None:
    response = client.post(
        "/v1/thin", json={"pixels": [[1]], "sample": "ring"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "contract_violation"


def test_unknown_sample_has_own_category() -> None:
    response = client.post("/v1/thin", json={"sample": "nope"})
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "unknown_sample"


def test_samples_listing() -> None:
    body = client.get("/v1/samples").json()
    assert {"ring", "bridge", "fork", "cross_tile_stroke"} <= set(body["samples"])
