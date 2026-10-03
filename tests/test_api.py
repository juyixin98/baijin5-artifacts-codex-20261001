"""API integration tests via FastAPI TestClient."""

from __future__ import annotations

import base64
import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.main import app
from conftest import fork_image, ring_image

client = TestClient(app)


def test_health_reports_version_and_config():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]
    assert body["config"]["tile_size"] > 0


def test_skeletonize_fork_end_to_end():
    r = client.post(
        "/skeletonize",
        json={"pixels": fork_image().tolist(), "mode": "tiled", "tile_size": 6},
        headers={"x-request-id": "test-req-fork"},
    )
    assert r.status_code == 200
    assert r.headers["x-request-id"] == "test-req-fork"
    body = r.json()
    assert body["request_id"] == "test-req-fork"
    assert body["validation"]["status"] == "pass"
    assert body["validation"]["metrics"]["endpoints_skeleton"] == 3
    assert body["validation"]["failure_reasons"] == []
    assert body["total_deleted"] > 0
    assert len(body["rounds"]) >= 2
    assert body["tiles"]["tile_count"] > 1
    assert body["tiles"]["halo_exchanges"] == 2 * len(body["rounds"])
    kinds = [n["kind"] for n in body["graph"]["nodes"]]
    assert kinds.count("endpoint") == 3
    assert kinds.count("junction") == 1
    assert len(body["graph"]["edges"]) == 3
    # edge chains are real pixel coordinates
    for e in body["graph"]["edges"]:
        assert e["length"] == len(e["pixels"]) >= 2


def test_skeletonize_ring_via_binary_png():
    png = Image.fromarray(ring_image() * 255)
    buf = io.BytesIO()
    png.save(buf, format="PNG")
    r = client.post(
        "/skeletonize",
        json={"image_png_base64": base64.b64encode(buf.getvalue()).decode()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["validation"]["status"] == "pass"
    assert body["validation"]["metrics"]["holes_skeleton"] == 1
    assert body["validation"]["metrics"]["endpoints_skeleton"] == 0


def test_skeletonize_rejects_non_binary_pixels():
    r = client.post("/skeletonize", json={"pixels": [[0, 5, 0], [0, 1, 0], [0, 0, 0]]})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["category"] == "NOT_BINARY"
    assert err["request_id"]


def test_skeletonize_rejects_antialiased_png():
    img = np.tile(np.arange(16, dtype=np.uint8) * 16, (16, 1))  # gradient
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    r = client.post(
        "/skeletonize",
        json={"image_png_base64": base64.b64encode(buf.getvalue()).decode()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "NOT_BINARY"


def test_validate_detects_broken_skeleton():
    original = fork_image()
    broken = original.copy()
    broken[10, :] = 0  # cut the stem -> extra component
    r = client.post(
        "/validate",
        json={"original": original.tolist(), "skeleton": broken.tolist()},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "fail"
    assert "components_equal" in body["failure_reasons"]


def test_validate_rejects_shape_mismatch():
    r = client.post(
        "/validate",
        json={
            "original": np.zeros((5, 5), dtype=int).tolist(),
            "skeleton": np.zeros((6, 6), dtype=int).tolist(),
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "SHAPE_MISMATCH"
