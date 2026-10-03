"""API tests via FastAPI TestClient: happy path, contract violations,
request-id propagation, fixtures and validation endpoints."""

import base64
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.fixtures import build_fixtures, generate_all
from app.main import app, settings


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures_on_disk():
    generate_all(settings.fixtures_dir)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _b64(arr: np.ndarray) -> str:
    buf = BytesIO()
    Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), mode="L").save(
        buf, format="PNG"
    )
    return base64.b64encode(buf.getvalue()).decode()


def _fixture_payload(name: str) -> dict:
    fx = {f.name: f for f in build_fixtures()}[name]
    return {"image_a": _b64(fx.img_a), "image_b": _b64(fx.img_b)}


def test_health_reports_versions(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    for pkg in ("app", "python", "numpy", "scipy", "pillow"):
        assert versions[pkg]


def test_estimate_integer_shift(client):
    resp = client.post("/v1/estimate", json=_fixture_payload("integer_shift"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["shift"]["dy"] == pytest.approx(12.0, abs=0.2)
    assert body["shift"]["dx"] == pytest.approx(-7.0, abs=0.2)
    assert body["failures"] == []
    assert body["diagnostics"]["versions"]["numpy"]
    # Request id is echoed in header and body.
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_estimate_constant_image_failed_listed_separately(client):
    resp = client.post("/v1/estimate", json=_fixture_payload("constant_image"))
    body = resp.json()
    assert body["status"] == "failed"
    assert "DEGENERATE_SPECTRUM" in body["failures"]
    assert body["shift"] is None


def test_estimate_periodic_uncertain_with_ambiguity(client):
    resp = client.post("/v1/estimate", json=_fixture_payload("periodic_texture"))
    body = resp.json()
    assert body["status"] == "uncertain"
    assert "AMBIGUOUS_PEAKS" in body["uncertainties"]
    assert len(body["peaks"]) >= 2
    # Ambiguity peaks are reported individually, not just as a flag.
    assert len(body["ambiguity_peaks"]) >= 2
    top = body["ambiguity_peaks"][0]["value"]
    assert body["ambiguity_peaks"][1]["value"] / top > 0.8


def test_estimate_rejects_shape_mismatch(client):
    payload = _fixture_payload("integer_shift")
    payload["image_b"] = _b64(np.zeros((64, 64)))
    resp = client.post("/v1/estimate", json=payload)
    assert resp.status_code == 422
    assert "shape mismatch" in resp.json()["detail"]
    assert resp.json()["request_id"]


def test_estimate_rejects_non_grayscale(client):
    buf = BytesIO()
    Image.fromarray(np.zeros((32, 32, 3), dtype=np.uint8), mode="RGB").save(
        buf, format="PNG"
    )
    payload = _fixture_payload("integer_shift")
    payload["image_a"] = base64.b64encode(buf.getvalue()).decode()
    resp = client.post("/v1/estimate", json=payload)
    assert resp.status_code == 422
    assert "grayscale" in resp.json()["detail"]


def test_estimate_rejects_bad_base64(client):
    payload = _fixture_payload("integer_shift")
    payload["image_a"] = "!!!not-base64!!!"
    resp = client.post("/v1/estimate", json=payload)
    assert resp.status_code == 422


def test_tiled_estimate(client):
    resp = client.post("/v1/estimate/tiled", json=_fixture_payload("integer_shift"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "uncertain")
    assert body["tiles_total"] >= 1
    assert body["global_shift"]["dy"] == pytest.approx(12.0, abs=0.5)
    assert body["global_shift"]["dx"] == pytest.approx(-7.0, abs=0.5)


def test_fixtures_listing_and_detail(client):
    resp = client.get("/v1/fixtures")
    assert resp.status_code == 200
    names = {f["name"] for f in resp.json()}
    assert "subpixel_shift" in names

    resp = client.get("/v1/fixtures/subpixel_shift")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ground_truth_shift"] == {"dy": 5.4, "dx": -3.65}
    assert body["image_a"] and body["image_b"]

    resp = client.get("/v1/fixtures/does_not_exist")
    assert resp.status_code == 422


def test_validation_endpoint(client):
    resp = client.post("/v1/validation/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["summary"]["all_passed"]
    assert body["request_id"]
