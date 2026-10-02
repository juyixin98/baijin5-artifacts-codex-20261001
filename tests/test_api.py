"""API tests: endpoints, failure categories over HTTP, diagnostics."""

from __future__ import annotations

import base64
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import create_app
from app.config import Settings


@pytest.fixture()
def client():
    app = create_app(Settings(max_image_side=64))
    with TestClient(app) as c:
        yield c


def _png_bytes(array: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(array, mode="L").save(buf, format="PNG")
    return buf.getvalue()


def _upload(marker: np.ndarray, mask: np.ndarray) -> dict[str, bytes]:
    return {
        "marker": ("marker.png", _png_bytes(marker), "image/png"),
        "mask": ("mask.png", _png_bytes(mask), "image/png"),
    }


def _valid_pair():
    mask = np.full((10, 10), 200, dtype=np.uint8)
    marker = np.zeros_like(mask)
    marker[5, 5] = 120
    return marker, mask


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.parametrize("engine", ["queue", "reference", "tiled"])
def test_reconstruct_json_all_engines(client, engine):
    marker, mask = _valid_pair()
    resp = client.post(
        f"/v1/reconstruct?engine={engine}", files=_upload(marker, mask)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    diag = body["diagnostics"]
    assert diag["status"] == "accepted"
    assert diag["engine"] == engine
    assert diag["request_id"]
    assert resp.headers["X-Request-Id"] == diag["request_id"]

    result_png = base64.b64decode(body["result_png_b64"])
    result = np.asarray(Image.open(io.BytesIO(result_png)))
    assert list(result.shape) == body["result_shape"] == [10, 10]
    assert np.all(result == 120)  # flat mask floods at seed level


def test_reconstruct_png_response(client):
    marker, mask = _valid_pair()
    resp = client.post(
        "/v1/reconstruct?response_format=png", files=_upload(marker, mask)
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert "X-Request-Id" in resp.headers
    result = np.asarray(Image.open(io.BytesIO(resp.content)))
    assert np.all(result == 120)


def test_violation_rejected_422_with_category(client):
    marker, mask = _valid_pair()
    marker[0, 0] = 255  # > mask 200
    resp = client.post("/v1/reconstruct", files=_upload(marker, mask))
    assert resp.status_code == 422
    diag = resp.json()["diagnostics"]
    assert diag["status"] == "rejected"
    assert diag["failure_category"] == "marker_exceeds_mask"
    assert diag["violation_pixels"] == 1


def test_violation_clip_policy(client):
    marker, mask = _valid_pair()
    marker[0, 0] = 255
    resp = client.post(
        "/v1/reconstruct?on_violation=clip", files=_upload(marker, mask)
    )
    assert resp.status_code == 200
    diag = resp.json()["diagnostics"]
    assert diag["status"] == "clipped"
    assert diag["violation_pixels"] == 1
    result_png = base64.b64decode(resp.json()["result_png_b64"])
    result = np.asarray(Image.open(io.BytesIO(result_png)))
    assert np.all(result <= mask)


def test_shape_mismatch_422(client):
    marker, mask = _valid_pair()
    resp = client.post(
        "/v1/reconstruct", files=_upload(marker, mask[:-1])
    )
    assert resp.status_code == 422
    assert (
        resp.json()["diagnostics"]["failure_category"] == "shape_mismatch"
    )


def test_undecodable_upload_400(client):
    _, mask = _valid_pair()
    files = {
        "marker": ("marker.png", b"not a png at all", "image/png"),
        "mask": ("mask.png", _png_bytes(mask), "image/png"),
    }
    resp = client.post("/v1/reconstruct", files=files)
    assert resp.status_code == 400
    diag = resp.json()["diagnostics"]
    assert diag["status"] == "undetermined"
    assert diag["failure_category"] == "decode_error"


def test_rgb_upload_400(client):
    rgb = np.zeros((6, 6, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buf, format="PNG")
    _, mask = _valid_pair()
    files = {
        "marker": ("marker.png", buf.getvalue(), "image/png"),
        "mask": ("mask.png", _png_bytes(mask), "image/png"),
    }
    resp = client.post("/v1/reconstruct", files=files)
    assert resp.status_code == 400
    assert resp.json()["diagnostics"]["failure_category"] == "not_grayscale"


def test_validate_endpoint(client):
    marker, mask = _valid_pair()
    resp = client.post("/v1/validate", files=_upload(marker, mask))
    assert resp.status_code == 200
    body = resp.json()
    assert body["diagnostics"]["status"] == "accepted"
    assert body["marker_stats"]["nonzero"] == 1
    assert body["mask_stats"]["max"] == 200


def test_validate_endpoint_reports_violation(client):
    marker, mask = _valid_pair()
    marker[2, 2] = 255
    resp = client.post("/v1/validate", files=_upload(marker, mask))
    assert resp.status_code == 200  # validate never rejects via HTTP
    diag = resp.json()["diagnostics"]
    assert diag["status"] == "rejected"
    assert diag["failure_category"] == "marker_exceeds_mask"


def test_bad_connectivity_value_422(client):
    marker, mask = _valid_pair()
    resp = client.post(
        "/v1/reconstruct?connectivity=6", files=_upload(marker, mask)
    )
    assert resp.status_code == 422  # pydantic enum validation
