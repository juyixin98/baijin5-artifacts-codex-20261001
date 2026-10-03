"""API contract tests: success envelope, failure categories, versions."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import create_app
from app.config import Settings
from tests.conftest import fixture_b64


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings().validate()))


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "data": {"status": "up"}}


def test_version_reports_dependencies(client):
    resp = client.get("/v1/version")
    assert resp.status_code == 200
    versions = resp.json()["data"]["versions"]
    for key in ("python", "numpy", "scipy", "pillow", "fastapi", "service"):
        assert key in versions and versions[key]


class TestSeamEndpoint:
    def test_step_image_seam(self, client):
        resp = client.post("/v1/seam", json={"image_b64": fixture_b64("step_5x6.png")})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["path_original_cols"] == [0, 0, 0, 0, 0]
        assert data["energy"] == 0.0
        assert data["mode"] == "gradient"
        assert data["run_id"]
        assert data["input_sha256"]
        assert "numpy" in data["versions"]

    def test_forward_mode_hand_computed(self, client):
        resp = client.post(
            "/v1/seam",
            json={"image_b64": fixture_b64("forward_2x3.png"), "energy_mode": "forward"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["path_original_cols"] == [1, 1]
        assert data["energy"] == 0.0
        assert data["mode"] == "forward"

    def test_full_row_protection_rejected(self, client):
        resp = client.post(
            "/v1/seam",
            json={
                "image_b64": fixture_b64("step_5x6.png"),
                "protect_mask_b64": fixture_b64("mask_row_5x6.png"),
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["category"] == "NO_LEGAL_SEAM"
        assert body["error"]["run_id"]

    def test_fully_protected_rejected(self, client):
        resp = client.post(
            "/v1/seam",
            json={
                "image_b64": fixture_b64("step_5x6.png"),
                "protect_mask_b64": fixture_b64("mask_full_5x6.png"),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "NO_LEGAL_SEAM"

    def test_invalid_base64_rejected(self, client):
        resp = client.post("/v1/seam", json={"image_b64": "!!!not-base64!!!"})
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "INVALID_IMAGE"

    def test_undecodable_image_rejected(self, client):
        payload = base64.b64encode(b"this is not an image").decode()
        resp = client.post("/v1/seam", json={"image_b64": payload})
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "INVALID_IMAGE"

    def test_mask_shape_mismatch_rejected(self, client):
        resp = client.post(
            "/v1/seam",
            json={
                "image_b64": fixture_b64("step_5x6.png"),
                "protect_mask_b64": fixture_b64("flat_4x4.png"),
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "MASK_SHAPE_MISMATCH"

    def test_protect_rect_out_of_bounds_rejected(self, client):
        resp = client.post(
            "/v1/seam",
            json={
                "image_b64": fixture_b64("step_5x6.png"),
                "protect_rects": [{"row": 4, "col": 4, "height": 5, "width": 5}],
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "MASK_SHAPE_MISMATCH"

    def test_forward_with_displacement_2_rejected(self, client):
        resp = client.post(
            "/v1/seam",
            json={
                "image_b64": fixture_b64("step_5x6.png"),
                "energy_mode": "forward",
                "max_displacement": 2,
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "INVALID_CONFIG"


class TestCarveEndpoint:
    def test_consecutive_removals_original_coordinates(self, client):
        resp = client.post(
            "/v1/carve",
            json={"image_b64": fixture_b64("step_5x6.png"), "num_seams": 3},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [s["path_original_cols"] for s in data["seams"]] == [
            [0, 0, 0, 0, 0],
            [1, 1, 1, 1, 1],
            [4, 4, 4, 4, 4],
        ]
        assert [s["energy"] for s in data["seams"]] == [0.0, 0.0, 0.0]
        final = Image.open(io.BytesIO(base64.b64decode(data["final_image_b64"])))
        assert final.size == (3, 5)  # PIL reports (width, height)

    def test_num_seams_exceeding_width_rejected(self, client):
        resp = client.post(
            "/v1/carve",
            json={"image_b64": fixture_b64("spike_4x3.png"), "num_seams": 3},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "INVALID_REQUEST"

    def test_tied_image_deterministic(self, client):
        resp = client.post(
            "/v1/carve",
            json={"image_b64": fixture_b64("tied_4x4.png"), "num_seams": 1},
        )
        assert resp.status_code == 200
        seam = resp.json()["data"]["seams"][0]
        assert seam["path_original_cols"] == [0, 0, 0, 0]
        assert seam["energy"] == 0.0
