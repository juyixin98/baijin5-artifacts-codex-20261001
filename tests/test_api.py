"""API tests: concrete results and failure categories via FastAPI TestClient.

Success cases assert exact seams/energies (hand-derived references);
failure cases assert HTTP status AND the stable error category -- never
just "the endpoint responded".
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from seamcarve.api import app

HAND_4x4_PIXELS = [
    [10, 20, 30, 40],
    [10, 20, 30, 40],
    [50, 60, 70, 80],
    [50, 60, 70, 80],
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAMCARVE_LOG_DIR", str(tmp_path))
    return TestClient(app, raise_server_exceptions=False)


def test_health_and_version(client):
    assert client.get("/v1/health").json() == {"status": "ok"}
    versions = client.get("/v1/version").json()["versions"]
    assert {"python", "numpy", "scipy", "pillow", "seamcarve"} <= set(versions)


def test_find_seam_gradient_exact(client, run_logger):
    resp = client.post("/v1/seam/find", json={"pixels": HAND_4x4_PIXELS})
    run_logger.emit("assertion", step="api", basis="hand-derived seam via HTTP",
                    endpoint="/v1/seam/find", status=resp.status_code)
    assert resp.status_code == 200
    body = resp.json()
    assert body["seam"]["columns"] == [0, 0, 0, 0]
    assert body["seam"]["points"] == [[0, 0], [1, 0], [2, 0], [3, 0]]
    assert body["seam"]["energy"] == pytest.approx(120.0)
    assert body["energy_mode"] == "gradient"
    assert body["run_id"] and body["input_sha256"] and body["versions"]


def test_find_seam_forward_exact(client):
    resp = client.post(
        "/v1/seam/find", json={"pixels": HAND_4x4_PIXELS, "energy_mode": "forward"}
    )
    assert resp.status_code == 200
    assert resp.json()["seam"]["energy"] == pytest.approx(30.0)


def test_carve_returns_original_coordinate_paths(client, run_logger):
    resp = client.post(
        "/v1/carve",
        json={"pixels": HAND_4x4_PIXELS, "n_seams": 2, "chunk_size": 1},
    )
    run_logger.emit("assertion", step="api", basis="hand-derived 2-seam carve via HTTP",
                    endpoint="/v1/carve", status=resp.status_code)
    assert resp.status_code == 200
    body = resp.json()
    assert body["final_width"] == 2
    assert body["seams"][0]["points"] == [[0, 0], [1, 0], [2, 0], [3, 0]]
    assert body["seams"][1]["points"] == [[0, 1], [1, 1], [2, 1], [3, 1]]
    assert [c["seams_done"] for c in body["chunks"]] == [1, 2]


def test_no_legal_seam_returns_409_with_category(client, fixture_loader, tmp_path):
    _, mask, _ = fixture_loader("protected_row_6x6")
    image, _, _ = fixture_loader("protected_row_6x6")
    resp = client.post(
        "/v1/seam/find",
        json={"pixels": image.tolist(), "protect_mask": mask.astype(int).tolist()},
    )
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["category"] == "NO_LEGAL_SEAM"
    # the error's run_id must correlate with a run log on disk
    log_file = tmp_path / f"api-{error['run_id']}.log"
    assert log_file.exists()
    assert error["run_id"] in log_file.read_text()


def test_mask_shape_mismatch_is_contract_violation(client):
    resp = client.post(
        "/v1/seam/find",
        json={"pixels": HAND_4x4_PIXELS, "protect_mask": [[0, 0], [0, 0]]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "CONTRACT_VIOLATION"


def test_mask_values_must_be_binary(client):
    resp = client.post(
        "/v1/seam/find",
        json={"pixels": HAND_4x4_PIXELS,
              "protect_mask": [[0, 2, 0, 0]] * 4},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "CONTRACT_VIOLATION"


def test_pixel_out_of_range_is_contract_violation(client):
    bad = [row[:] for row in HAND_4x4_PIXELS]
    bad[0][0] = 300
    resp = client.post("/v1/seam/find", json={"pixels": bad})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "CONTRACT_VIOLATION"


def test_ragged_pixels_are_contract_violation(client):
    resp = client.post("/v1/seam/find", json={"pixels": [[1, 2], [3]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "CONTRACT_VIOLATION"


def test_unknown_energy_mode_is_request_validation(client):
    resp = client.post(
        "/v1/seam/find", json={"pixels": HAND_4x4_PIXELS, "energy_mode": "magic"}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "REQUEST_VALIDATION"


def test_excessive_seam_count_is_contract_violation(client):
    resp = client.post(
        "/v1/carve", json={"pixels": HAND_4x4_PIXELS, "n_seams": 4}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "CONTRACT_VIOLATION"


def test_failures_never_return_success_status(client, fixture_loader):
    image, mask, _ = fixture_loader("protected_row_6x6")
    resp = client.post(
        "/v1/carve",
        json={"pixels": image.tolist(),
              "protect_mask": mask.astype(int).tolist(), "n_seams": 1},
    )
    assert resp.status_code == 409
    assert "error" in resp.json()
