"""HTTP interface tests: exact results, categorized rejections, request ids,
and the PNG round-trip."""
import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from fixtures import ALL_FIXTURES
from geodesic_recon.config import Settings
from geodesic_recon.service import create_app


@pytest.fixture()
def client():
    return TestClient(create_app(Settings()))


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["config"]["boundary_rule"] == "edge-ignore"


def test_reconstruct_exact_result_and_trace(client):
    fx = ALL_FIXTURES[2]()  # flat_zone, hand-computed expected = all 60
    resp = client.post(
        "/v1/reconstruct",
        json={"marker": fx.marker.tolist(), "mask": fx.mask.tolist()},
    )
    assert resp.status_code == 200
    body = resp.json()
    np.testing.assert_array_equal(np.asarray(body["result"]), fx.expected)
    assert body["clipped"] is False
    assert body["trace"]["algorithm"] == "queue"
    assert body["trace"]["pops"] > 0
    assert body["validation"]["ok"] is True
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_request_id_echoed_from_header(client):
    fx = ALL_FIXTURES[2]()
    resp = client.post(
        "/v1/reconstruct",
        json={"marker": fx.marker.tolist(), "mask": fx.mask.tolist()},
        headers={"X-Request-ID": "test-req-123"},
    )
    assert resp.headers["X-Request-ID"] == "test-req-123"
    assert resp.json()["request_id"] == "test-req-123"


def test_marker_exceeds_mask_rejected_422_with_category(client):
    resp = client.post(
        "/v1/reconstruct",
        json={"marker": [[5.0]], "mask": [[3.0]]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "MARKER_EXCEEDS_MASK"
    assert body["request_id"]


def test_marker_exceeds_mask_clipped_on_request(client):
    resp = client.post(
        "/v1/reconstruct",
        json={"marker": [[5.0, 1.0]], "mask": [[3.0, 2.0]], "on_violation": "clip"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["clipped"] is True
    np.testing.assert_array_equal(np.asarray(body["result"]), [[3.0, 2.0]])


def test_shape_mismatch_422(client):
    resp = client.post(
        "/v1/reconstruct",
        json={"marker": [[1.0, 2.0]], "mask": [[1.0], [2.0]]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "SHAPE_MISMATCH"


def test_nan_rejected_422(client):
    # JSON has no NaN literal; send the raw body (Python's decoder accepts it).
    resp = client.post(
        "/v1/reconstruct",
        content='{"marker": [[NaN]], "mask": [[1.0]]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "NON_FINITE"


@pytest.mark.parametrize("algorithm", ["sync", "queue", "tiled"])
def test_all_algorithms_selectable_and_equal(client, algorithm):
    fx = ALL_FIXTURES[0]()  # thin_bridge
    resp = client.post(
        "/v1/reconstruct",
        json={
            "marker": fx.marker.tolist(),
            "mask": fx.mask.tolist(),
            "algorithm": algorithm,
        },
    )
    assert resp.status_code == 200
    np.testing.assert_array_equal(np.asarray(resp.json()["result"]), fx.expected)


def test_validate_endpoint_accepts_genuine_result(client):
    fx = ALL_FIXTURES[0]()
    genuine = client.post(
        "/v1/reconstruct", json={"marker": fx.marker.tolist(), "mask": fx.mask.tolist()}
    ).json()["result"]
    resp = client.post(
        "/v1/validate",
        json={"marker": fx.marker.tolist(), "mask": fx.mask.tolist(), "result": genuine},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_validate_endpoint_flags_tampered_result(client):
    fx = ALL_FIXTURES[2]()
    tampered = fx.expected.copy()
    tampered[0, 0] = 10.0  # breaks the fixed point
    resp = client.post(
        "/v1/validate",
        json={"marker": fx.marker.tolist(), "mask": fx.mask.tolist(), "result": tampered.tolist()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    failed = {c["name"] for c in body["checks"] if not c["passed"]}
    assert "fixed_point" in failed


def _png_bytes(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8), mode="L").save(buf, format="PNG")
    return buf.getvalue()


def test_image_endpoint_png_roundtrip(client):
    fx = ALL_FIXTURES[2]()  # values fit in uint8
    resp = client.post(
        "/v1/reconstruct/image",
        files={
            "marker": ("marker.png", _png_bytes(fx.marker), "image/png"),
            "mask": ("mask.png", _png_bytes(fx.mask), "image/png"),
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    result = np.asarray(Image.open(io.BytesIO(resp.content)))
    np.testing.assert_array_equal(result.astype(float), fx.expected)


def test_image_endpoint_rejects_undecodable(client):
    resp = client.post(
        "/v1/reconstruct/image",
        files={
            "marker": ("marker.png", b"not a png", "image/png"),
            "mask": ("mask.png", _png_bytes(np.ones((2, 2))), "image/png"),
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "MALFORMED_IMAGE"
