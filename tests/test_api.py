"""End-to-end HTTP tests through FastAPI's TestClient."""
from __future__ import annotations

import base64
import io
import math

import numpy as np
import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app.api import app
from app.config import Settings
from app import service as service_module


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def png_b64(mask: np.ndarray) -> str:
    buf = io.BytesIO()
    Image.fromarray((mask.astype(np.uint8) * 255), mode="L").save(
        buf, format="PNG"
    )
    return base64.b64encode(buf.getvalue()).decode()


def test_health_reports_versions(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "kernel_version" in body and body["kernel_version"]


def test_concrete_distance_and_source_grid(client):
    mask = np.zeros((3, 3), dtype=bool)
    mask[1, 1] = True
    r = client.post("/v1/edt", json={
        "image_base64": png_b64(mask), "request_id": "rid-concrete",
    })
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["request_id"] == "rid-concrete"
    assert b["stats"]["source_count"] == 1
    assert b["stats"]["has_sources"] is True
    assert b["distance_grid"][0][0] == pytest.approx(math.sqrt(2))
    assert b["distance_grid"][1][1] == 0.0
    assert b["nearest_y_grid"] == [[1] * 3 for _ in range(3)]
    assert b["nearest_x_grid"] == [[1, 1, 1] for _ in range(3)]
    assert b["execution"]["path"] == "direct"
    assert {s["name"] for s in b["execution"]["steps"]} >= {"decode", "edt"}


def test_tie_is_reported_as_warning_not_error(client):
    mask = np.zeros((1, 5), dtype=bool)
    mask[0, 0] = mask[0, 4] = True
    r = client.post("/v1/edt/verify", json={"image_base64": png_b64(mask)})
    assert r.status_code == 200
    b = r.json()
    assert b["distance_grid"][0][2] == pytest.approx(2.0)
    assert b["nearest_x_grid"][0][2] == 0  # lex-smallest source
    codes = {w["code"] for w in b["warnings"]}
    assert "EQUI_DISTANT_TIE" in codes
    assert b["failures"] == []
    assert b["execution"]["verification"]["nearest_source_mismatch_count"] == 0


def test_no_sources_is_success_with_null_distances(client):
    """Failure category: empty input misreported as an error or zeros."""
    mask = np.zeros((3, 4), dtype=bool)
    r = client.post("/v1/edt", json={"image_base64": png_b64(mask)})
    assert r.status_code == 200
    b = r.json()
    assert b["stats"]["has_sources"] is False
    assert b["stats"]["source_count"] == 0
    assert b["stats"]["min_distance"] is None
    assert all(v is None for row in b["distance_grid"] for v in row)
    assert all(v == -1 for row in b["nearest_y_grid"] for v in row)


def test_all_sources(client):
    mask = np.ones((2, 3), dtype=bool)
    r = client.post("/v1/edt", json={"image_base64": png_b64(mask),
                                     "spacing_y": 2.0, "spacing_x": 0.5})
    b = r.json()
    assert b["stats"]["all_sources"] is True
    assert b["stats"]["max_distance"] == 0.0


def test_verify_endpoint_passes_for_kernel_and_reports_method(client):
    rng = np.random.default_rng(5)
    mask = rng.random((6, 7)) < 0.3
    r = client.post("/v1/edt/verify", json={"image_base64": png_b64(mask)})
    assert r.status_code == 200
    v = r.json()["execution"]["verification"]
    assert v["performed"] is True
    assert v["method"] == "independent_brute_force_pixel_loop"
    assert v["pixels_checked"] == 42
    assert v["distance_mismatch_count"] == 0
    assert v["nearest_source_mismatch_count"] == 0
    assert v["max_relative_distance_error"] == pytest.approx(0.0, abs=1e-12)


def test_anisotropic_spacing_endpoint(client):
    mask = np.zeros((3, 5), dtype=bool)
    mask[0, 0] = True
    r = client.post("/v1/edt", json={
        "image_base64": png_b64(mask), "spacing_y": 2.0, "spacing_x": 0.5,
    })
    b = r.json()
    assert b["distance_grid"][2][4] == pytest.approx(math.hypot(4.0, 2.0))


def test_force_tiled_path_matches(client):
    mask = np.zeros((5, 5), dtype=bool)
    mask[0, 0] = mask[4, 4] = True
    r = client.post("/v1/edt", json={
        "image_base64": png_b64(mask), "force_tiled": True,
    })
    assert r.status_code == 200
    b = r.json()
    assert b["execution"]["path"] == "tiled"
    assert b["execution"]["tiles"] >= 1
    assert b["distance_grid"][0][4] == pytest.approx(4.0)
    assert b["nearest_y_grid"][2][2] == 0  # tie at centre: lex-smallest


def test_bad_base64_is_400_invalid_image(client):
    r = client.post("/v1/edt", json={"image_base64": "@@@"})
    assert r.status_code == 400
    f = r.json()["failures"][0]
    assert f["code"] == "INVALID_IMAGE"
    assert f["location"] == "body.image_base64"


def test_non_image_payload_is_400_invalid_image(client):
    payload = base64.b64encode(b"not a png").decode()
    r = client.post("/v1/edt", json={"image_base64": payload})
    assert r.status_code == 400
    assert r.json()["failures"][0]["code"] == "INVALID_IMAGE"


def test_nonpositive_spacing_rejected_by_contract(client):
    mask = np.ones((2, 2), dtype=bool)
    r = client.post("/v1/edt", json={
        "image_base64": png_b64(mask), "spacing_y": 0,
    })
    assert r.status_code == 422  # pydantic Field constraint


def test_too_large_returns_413_with_category(client, monkeypatch):
    monkeypatch.setattr(
        service_module, "default_settings",
        Settings(max_edge_px=2, max_total_cells=100),
    )
    mask = np.ones((3, 3), dtype=bool)
    r = client.post("/v1/edt", json={"image_base64": png_b64(mask)})
    assert r.status_code == 413
    assert r.json()["failures"][0]["code"] == "TOO_LARGE"


def test_request_id_is_echoed_on_error(client):
    r = client.post("/v1/edt", json={
        "image_base64": "@@@", "request_id": "rid-err-123",
    })
    assert r.json()["request_id"] == "rid-err-123"
