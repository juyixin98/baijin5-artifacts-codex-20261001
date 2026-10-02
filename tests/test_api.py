"""API tests: assert concrete payloads and failure categories, not just
that the endpoint responds."""

import math

import pytest
from fastapi.testclient import TestClient

from edt_service.api import app
from edt_service.errors import ErrorCategory

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_version_endpoint_reports_components():
    resp = client.get("/v1/version")
    body = resp.json()
    for component in ("edt_service", "numpy", "scipy", "pillow", "fastapi"):
        assert component in body


def test_happy_path_exact_values():
    resp = client.post("/v1/edt", json={"grid": [[1, 0], [0, 0]]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "direct"
    assert body["distances"][0] == [0.0, 1.0]
    assert body["distances"][1][0] == 1.0
    assert body["distances"][1][1] == pytest.approx(math.sqrt(2.0))
    assert body["labels"] == [[0, 0], [0, 0]]
    assert body["request_id"]
    assert "edt_service" in body["versions"]


def test_request_id_echoed_from_body():
    resp = client.post(
        "/v1/edt", json={"grid": [[1]], "request_id": "demo-req-1"}
    )
    assert resp.json()["request_id"] == "demo-req-1"
    assert resp.headers["x-request-id"]


def test_empty_source_grid_returns_nulls_not_error():
    resp = client.post("/v1/edt", json={"grid": [[0, 0], [0, 0]]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "degenerate-empty"
    assert body["distances"] == [[None, None], [None, None]]
    assert body["labels"] == [[None, None], [None, None]]


def test_full_source_grid():
    resp = client.post("/v1/edt", json={"grid": [[1, 1], [1, 1]]})
    body = resp.json()
    assert body["mode"] == "degenerate-full"
    assert body["distances"] == [[0.0, 0.0], [0.0, 0.0]]
    assert body["labels"] == [[0, 1], [2, 3]]


def test_anisotropic_spacing_over_wire():
    resp = client.post(
        "/v1/edt", json={"grid": [[1], [0]], "spacing": [3.0, 1.0]}
    )
    assert resp.json()["distances"] == [[0.0], [3.0]]


def test_tie_break_over_wire():
    resp = client.post("/v1/edt", json={"grid": [[1, 0, 1]]})
    body = resp.json()
    assert body["distances"] == [[0.0, 1.0, 0.0]]
    assert body["labels"] == [[0, 0, 2]]


def test_forced_tiling_matches_direct():
    grid = ( __import__("numpy").random.default_rng(5).random((24, 24)) < 0.1 ).astype(int).tolist()
    direct = client.post("/v1/edt", json={"grid": grid}).json()
    tiled = client.post("/v1/edt", json={"grid": grid, "tile_size": 8}).json()
    assert tiled["mode"] == "tiled"
    assert len(tiled["tiles"]) == 9
    assert tiled["distances"] == direct["distances"]
    assert tiled["labels"] == direct["labels"]


def test_ragged_grid_categorized():
    resp = client.post("/v1/edt", json={"grid": [[1, 0], [1]]})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["category"] == ErrorCategory.INVALID_GRID
    assert err["request_id"]


def test_non_binary_categorized():
    resp = client.post("/v1/edt", json={"grid": [[1, 7]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == ErrorCategory.INVALID_GRID


def test_bad_spacing_categorized():
    resp = client.post("/v1/edt", json={"grid": [[1]], "spacing": [0, 1]})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == ErrorCategory.INVALID_SPACING


def test_bad_tile_size_categorized():
    resp = client.post("/v1/edt", json={"grid": [[1]], "tile_size": -1})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == ErrorCategory.INVALID_OPTION


def test_schema_error_categorized():
    resp = client.post("/v1/edt", json={"spacing": [1, 1]})  # missing grid
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == ErrorCategory.INVALID_GRID


def test_error_response_shape_is_stable():
    resp = client.post("/v1/edt", json={"grid": [[3]]})
    err = resp.json()["error"]
    assert set(err) == {"category", "message", "request_id"}
