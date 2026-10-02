"""API tests: real HTTP round-trips through the FastAPI validation interface."""

from __future__ import annotations

import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from watershed_backend.api.app import create_app
from watershed_backend.config import Settings
from watershed_backend.imageio import encode_gradient_png
from tests.fixtures.synthetic import two_basin


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings(progress_chunk_pixels=4)))


def _payload() -> dict:
    gradient, seeds, _, _ = two_basin()
    return {
        "gradient": gradient.tolist(),
        "seeds": [{"row": s.row, "col": s.col, "label": s.label} for s in seeds],
        "connectivity": 8,
    }


def test_health_and_version(client):
    assert client.get("/v1/health").json() == {"status": "ok"}
    versions = client.get("/v1/version").json()
    for key in ("python", "numpy", "scipy", "pillow", "fastapi"):
        assert key in versions and versions[key]


def test_post_job_returns_201_and_hand_computed_labels(client):
    _, _, expected_labels, _ = two_basin()
    response = client.post("/v1/jobs", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "COMPLETED"
    assert body["error"] is None
    np.testing.assert_array_equal(
        np.array(body["result"]["labels"], dtype=np.int32), expected_labels
    )
    assert body["result"]["stats"]["boundary_pixels"] == 5
    assert body["result"]["algorithm"]["method"] == "meyer_immersion"
    assert body["input_sha256"]


def test_job_status_and_result_endpoints(client):
    created = client.post("/v1/jobs", json=_payload()).json()
    job_id = created["job_id"]
    status = client.get(f"/v1/jobs/{job_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "COMPLETED"
    assert "result" not in status.json()
    result = client.get(f"/v1/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.json()["result"]["stats"]["seed_pixels"] == 2


def test_unknown_job_is_404_not_success(client):
    assert client.get("/v1/jobs/nope").status_code == 404
    assert client.get("/v1/jobs/nope/result").status_code == 404


def test_seed_conflict_is_422_with_category(client):
    payload = _payload()
    payload["seeds"] = [
        {"row": 0, "col": 0, "label": 1},
        {"row": 0, "col": 0, "label": 2},
    ]
    response = client.post("/v1/jobs", json=payload)
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["error"]["category"] == "SEED_CONFLICT"


def test_empty_markers_is_422_with_category(client):
    payload = _payload()
    payload["markers"] = [[0] * 5 for _ in range(5)]
    del payload["seeds"]
    response = client.post("/v1/jobs", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["category"] == "EMPTY_MARKERS"


def test_missing_marker_source_is_schema_error(client):
    payload = _payload()
    del payload["seeds"]
    response = client.post("/v1/jobs", json=payload)
    assert response.status_code == 422


def test_failed_job_result_is_409(client):
    payload = _payload()
    payload["seeds"] = [
        {"row": 0, "col": 0, "label": 1},
        {"row": 0, "col": 0, "label": 2},
    ]
    job_id = client.post("/v1/jobs", json=payload).json()["job_id"]
    response = client.get(f"/v1/jobs/{job_id}/result")
    assert response.status_code == 409
    assert response.json()["error"]["category"] == "JOB_NOT_COMPLETED"


def test_png_gradient_endpoint_roundtrip(client):
    gradient, seeds, expected_labels, _ = two_basin()
    png_b64 = base64.b64encode(encode_gradient_png(gradient)).decode("ascii")
    response = client.post(
        "/v1/segment/image",
        json={
            "gradient_png_b64": png_b64,
            "seeds": [{"row": s.row, "col": s.col, "label": s.label} for s in seeds],
        },
    )
    assert response.status_code == 201
    np.testing.assert_array_equal(
        np.array(response.json()["result"]["labels"], dtype=np.int32),
        expected_labels,
    )


def test_invalid_png_is_422(client):
    response = client.post(
        "/v1/segment/image",
        json={
            "gradient_png_b64": base64.b64encode(b"not a png").decode("ascii"),
            "seeds": [{"row": 0, "col": 0, "label": 1}],
        },
    )
    assert response.status_code == 422
