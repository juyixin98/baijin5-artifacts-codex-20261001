"""API tests: endpoints, request-id correlation, error paths, validation report."""
import base64
import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app

client = TestClient(app)


def _png_b64(arr: np.ndarray) -> str:
    """Encode an array as 16-bit PNG; input may be 0-255 or 0-65535 scale."""
    scale = 256.0 if arr.max() <= 255.0 else 1.0
    q = np.clip(np.round(arr * scale), 0, 65535).astype(np.uint16)
    buf = io.BytesIO()
    Image.fromarray(q, mode="I;16").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_health_and_version():
    assert client.get("/v1/health").json() == {"status": "ok"}
    versions = client.get("/v1/version").json()
    assert set(versions) >= {"app", "kernel", "numpy", "scipy"}


def test_estimate_with_fixture_ids_and_request_id():
    resp = client.post("/v1/estimate", json={
        "request_id": "req-test-1",
        "reference": {"fixture_id": "integer_shift", "role": "reference"},
        "moving": {"fixture_id": "integer_shift", "role": "moving"},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req-test-1"
    assert body["status"] == "ok"
    assert abs(body["shift"]["dy"] - 5.0) < 0.15
    assert abs(body["shift"]["dx"] + 3.0) < 0.15
    # diagnostics: key steps are named and versions are reported
    step_names = [s["name"] for s in body["diagnostics"]["steps"]]
    assert "phase_correlation" in step_names
    assert "verify_overlap" in step_names
    assert set(body["versions"]) >= {"app", "kernel"}


def test_estimate_with_uploaded_pngs(pair):
    ref, mov, _ = pair("subpixel_shift")
    resp = client.post("/v1/estimate", json={
        "reference": {"png_base64": _png_b64(ref)},
        "moving": {"png_base64": _png_b64(mov)},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert abs(body["shift"]["dy"] - 2.4) < 0.15
    assert abs(body["shift"]["dx"] + 1.7) < 0.15


def test_estimate_shape_mismatch_returns_failure_not_500():
    rng = np.random.default_rng(0)
    a = rng.standard_normal((64, 64)) * 50 + 128
    b = rng.standard_normal((64, 32)) * 50 + 128
    resp = client.post("/v1/estimate", json={
        "reference": {"png_base64": _png_b64(a)},
        "moving": {"png_base64": _png_b64(b)},
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "shape_mismatch"


def test_estimate_rejects_bad_payload():
    resp = client.post("/v1/estimate", json={
        "reference": {"png_base64": "not-base64!!"},
        "moving": {"png_base64": "not-base64!!"},
    })
    assert resp.status_code == 400


def test_estimate_unknown_fixture_404():
    resp = client.post("/v1/estimate", json={
        "reference": {"fixture_id": "does_not_exist", "role": "reference"},
        "moving": {"fixture_id": "does_not_exist", "role": "moving"},
    })
    assert resp.status_code == 404


def test_validate_all_fixtures_categories_match():
    resp = client.post("/v1/validate", json={"tolerance_px": 0.5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["n_fixtures"] >= 7
    mismatches = [r for r in body["reports"] if not r["category_match"]]
    assert mismatches == [], f"category mismatches: {mismatches}"
    # localization error is reported for shifted fixtures
    by_id = {r["fixture_id"]: r for r in body["reports"]}
    assert by_id["integer_shift"]["localization_error_px"] < 0.15
    assert by_id["subpixel_shift"]["localization_error_px"] < 0.15
    assert by_id["subpixel_shift"]["within_tolerance"]


def test_validate_single_fixture():
    resp = client.post("/v1/validate", json={"fixture_id": "constant_image"})
    assert resp.status_code == 200
    report = resp.json()["reports"][0]
    assert report["observed_status"] == "failed"
    assert report["failure_reason"] == "flat_response"
    assert report["category_match"]


def test_tiled_endpoint(pair):
    resp = client.post("/v1/estimate/tiled", json={
        "reference": {"fixture_id": "integer_shift", "role": "reference"},
        "moving": {"fixture_id": "integer_shift", "role": "moving"},
        "tile_size": 64, "stride": 32,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert abs(body["shift"]["dy"] - 5.0) < 0.5
