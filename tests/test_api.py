"""API tests: contract validation, synchronous segmentation, error categories."""

from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image

from app.core.watershed import WATERSHED_LINE as W
from tests import fixtures


def _payload(elevation, markers, **extra):
    payload = {
        "elevation": np.asarray(elevation, dtype=float).tolist(),
        "markers": np.asarray(markers, dtype=int).tolist(),
    }
    payload.update(extra)
    return payload


def _png_b64(array: np.ndarray, mode: str = "L") -> str:
    image = Image.fromarray(np.asarray(array), mode=mode)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_reports_dependency_versions(client):
    response = client.get("/health")
    assert response.status_code == 200
    versions = response.json()["versions"]
    for key in ("python", "numpy", "scipy", "pillow", "fastapi", "app"):
        assert key in versions and versions[key], f"missing version for {key}"


# ---------------------------------------------------------------------------
# /v1/validate
# ---------------------------------------------------------------------------

def test_validate_accepts_well_formed_request(client):
    response = client.post("/v1/validate", json=_payload(*fixtures.saddle()[:2]))
    assert response.status_code == 200
    body = response.json()
    assert body == {"valid": True, "issues": []}


def test_validate_reports_no_seeds(client):
    response = client.post("/v1/validate", json=_payload(np.ones((2, 2)), np.zeros((2, 2), int)))
    body = response.json()
    assert body["valid"] is False
    assert any(issue["category"] == "no_seeds" for issue in body["issues"])


def test_validate_reports_shape_mismatch_and_negative_markers(client):
    payload = {"elevation": [[1.0, 2.0]], "markers": [[1, -1, 0]]}
    body = client.post("/v1/validate", json=payload).json()
    assert body["valid"] is False
    categories = {issue["category"] for issue in body["issues"]}
    assert "shape_mismatch" in categories


def test_validate_reports_schema_violation_for_bad_connectivity(client):
    payload = _payload(np.ones((2, 2)), [[1, 0], [0, 0]], connectivity=6)
    body = client.post("/v1/validate", json=payload).json()
    assert body["valid"] is False
    assert any(issue["category"] == "schema_violation" for issue in body["issues"])


def test_validate_reports_seed_outside_mask(client):
    payload = _payload(
        np.ones((2, 2)), [[1, 0], [0, 0]],
        mask=[[False, True], [True, True]],
    )
    body = client.post("/v1/validate", json=payload).json()
    assert body["valid"] is False
    assert any(issue["category"] == "seed_outside_mask" for issue in body["issues"])


# ---------------------------------------------------------------------------
# /v1/segment
# ---------------------------------------------------------------------------

def test_segment_returns_hand_computed_labels_and_boundary(client):
    response = client.post(
        "/v1/segment",
        json=_payload(fixtures.CORRIDOR_ELEVATION, fixtures.CORRIDOR_MARKERS, connectivity=4),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["labels"] == fixtures.CORRIDOR_EXPECTED.tolist()
    assert body["boundary"] == (fixtures.CORRIDOR_EXPECTED == W).tolist()
    assert body["run_id"]
    assert len(body["input_sha256"]) == 64
    assert body["stats"]["pixels_watershed"] == 2
    assert "flat index" in body["stats"]["tie_break_rule"]
    assert "numpy" in body["versions"]


def test_segment_is_deterministic_across_requests(client):
    elevation, markers = fixtures.noisy_gradient()
    payload = _payload(elevation, markers, connectivity=8)
    first = client.post("/v1/segment", json=payload).json()
    second = client.post("/v1/segment", json=payload).json()
    assert first["labels"] == second["labels"]
    assert first["input_sha256"] == second["input_sha256"]
    assert first["run_id"] != second["run_id"], "each run needs its own identity"


def test_segment_rejects_no_seeds_with_category(client):
    response = client.post("/v1/segment", json=_payload(np.ones((2, 2)), np.zeros((2, 2), int)))
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "no_seeds"


def test_segment_rejects_non_finite_elevation(client):
    # httpx refuses to serialise NaN, so post the raw JSON body (Python's
    # json parser on the server side accepts the NaN literal).
    response = client.post(
        "/v1/segment",
        content='{"elevation": [[1.0, NaN], [1.0, 1.0]], "markers": [[1, 0], [0, 0]]}',
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "non_finite_elevation"


def test_segment_rejects_oversized_payload(client, settings):
    settings_max = settings.max_image_pixels
    side = int(settings_max**0.5) + 1
    payload = _payload(np.ones((side, side)), np.zeros((side, side), int))
    payload["markers"][0][0] = 1
    response = client.post("/v1/segment", json=payload)
    assert response.status_code == 413
    assert response.json()["detail"]["category"] == "payload_too_large"


def test_segment_logs_run_identity_and_decision_basis(client, log_capture):
    from tests.conftest import events

    client.post(
        "/v1/segment",
        json=_payload(fixtures.PLATEAU_ELEVATION, fixtures.PLATEAU_MARKERS, connectivity=4),
    )
    started = events(log_capture, "run_started")
    finished = events(log_capture, "run_finished")
    assert len(started) == 1 and len(finished) == 1
    assert started[0]["run_id"] == finished[0]["run_id"]
    assert started[0]["input_sha256"] == finished[0]["input_sha256"]
    assert started[0]["seed_count"] == 2
    assert "versions" in started[0]
    assert "tie_break_rule" in finished[0]["stats"]


# ---------------------------------------------------------------------------
# /v1/segment/image (Pillow path)
# ---------------------------------------------------------------------------

def test_segment_image_from_png_uploads(client):
    # Two dark wells on a bright background -> gradient basins around them.
    image = np.full((9, 9), 200, dtype=np.uint8)
    image[4, 2] = 0
    image[4, 6] = 0
    markers = np.zeros((9, 9), dtype=np.uint8)
    markers[4, 2] = 1
    markers[4, 6] = 2
    response = client.post(
        "/v1/segment/image",
        json={
            "elevation_png_b64": _png_b64(image),
            "markers_png_b64": _png_b64(markers),
            "connectivity": 4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    labels = np.asarray(body["labels"])
    assert labels[4, 2] == 1 and labels[4, 6] == 2, "seeds must be preserved"
    assert set(np.unique(labels)) >= {1, 2}


def test_segment_image_rejects_rgb(client):
    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    markers = np.zeros((4, 4), dtype=np.uint8)
    markers[0, 0] = 1
    response = client.post(
        "/v1/segment/image",
        json={
            "elevation_png_b64": _png_b64(rgb, mode="RGB"),
            "markers_png_b64": _png_b64(markers),
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "unsupported_image_mode"


def test_segment_image_rejects_corrupt_base64(client):
    response = client.post(
        "/v1/segment/image",
        json={"elevation_png_b64": "!!!not-base64!!!", "markers_png_b64": "AAAA"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "invalid_base64"


def test_segment_image_rejects_shape_mismatch(client):
    image = np.zeros((4, 4), dtype=np.uint8)
    markers = np.zeros((5, 5), dtype=np.uint8)
    markers[0, 0] = 1
    response = client.post(
        "/v1/segment/image",
        json={"elevation_png_b64": _png_b64(image), "markers_png_b64": _png_b64(markers)},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["category"] == "shape_mismatch"
