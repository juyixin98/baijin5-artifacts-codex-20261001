"""End-to-end API tests: concrete payloads and distinguishable error
categories (400 input, 404 not-found, 409 conflict, 507 resource, 500
compute), never just 'the endpoint responds'."""

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pyramid_service.api import create_app
from pyramid_service.config import Settings
from pyramid_service.patterns import checkerboard, noise

from conftest import cascade_reference


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        store_dir=tmp_path / "api_store",
        max_source_pixels=4_000_000,
        max_region_pixels=4_096,
    )
    return TestClient(create_app(settings))


def _build(client, pid="api-p", pattern="checkerboard", w=65, h=33,
           tile_size=16, levels=4, kernel="area"):
    return client.post("/pyramids", json={
        "pyramid_id": pid,
        "source": {"kind": "synthetic", "pattern": pattern,
                   "width": w, "height": h},
        "tile_size": tile_size,
        "levels": levels,
        "kernel": kernel,
    })


def _npy_region(client, pid, level, x, y, w, h):
    resp = client.get(
        f"/pyramids/{pid}/region",
        params={"level": level, "x": x, "y": y, "w": w, "h": h, "format": "npy"},
    )
    return resp


def test_build_levels_and_region_roundtrip(client):
    resp = _build(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["pyramid_id"] == "api-p"
    assert body["run_id"]
    assert [(l["width"], l["height"]) for l in body["levels"]] == [
        (65, 33), (33, 17), (17, 9), (9, 5),
    ]

    levels = client.get("/pyramids/api-p/levels").json()["levels"]
    assert [l["level"] for l in levels] == [0, 1, 2, 3]
    assert all(l["run_id"] == body["run_id"] for l in levels)

    # Cross-tile region at level 1 (33x17, tile 16 -> 3x2 tiles).
    resp = _npy_region(client, "api-p", 1, 14, 3, 8, 6)
    assert resp.status_code == 200
    assert resp.headers["X-Region-Shape"] == "6,8,1"
    arr = np.load(io.BytesIO(resp.content))
    ref = cascade_reference(checkerboard(33, 65), 2, "area")[1]
    np.testing.assert_allclose(arr, ref[3:9, 14:22], atol=1e-12)


def test_region_png_response(client):
    _build(client, pid="png", pattern="checkerboard", w=32, h=32, levels=2)
    resp = client.get(
        "/pyramids/png/region",
        params={"level": 1, "x": 0, "y": 0, "w": 16, "h": 16, "format": "png"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(resp.content)))
    # 1px checkerboard area-averaged -> uniform mid-gray (127.5 -> 128).
    assert img.shape == (16, 16)
    np.testing.assert_array_equal(img, 128)


def test_gaussian_kernel_via_api(client):
    _build(client, pid="g", pattern="noise", w=20, h=14, tile_size=8,
           levels=2, kernel="gaussian")
    resp = _npy_region(client, "g", 1, 0, 0, 10, 7)
    assert resp.status_code == 200
    arr = np.load(io.BytesIO(resp.content))
    ref = cascade_reference(noise(14, 20, seed=0), 2, "gaussian")[1]
    np.testing.assert_allclose(arr, ref, atol=1e-12)


def test_error_categories_are_distinguishable(client):
    _build(client, pid="err", w=32, h=32, levels=2)

    # 409 state_conflict: rebuilding the same pyramid id.
    resp = _build(client, pid="err", w=32, h=32, levels=2)
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "state_conflict"

    # 404 not_found: level never published.
    resp = _npy_region(client, "err", 9, 0, 0, 2, 2)
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "not_found"

    # 400 input_error: non-positive size / out-of-bounds origin / bad pattern.
    resp = _npy_region(client, "err", 0, 0, 0, 0, 4)
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"
    resp = _npy_region(client, "err", 0, 32, 0, 4, 4)
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"
    resp = client.post("/pyramids", json={
        "source": {"kind": "synthetic", "pattern": "plaid",
                   "width": 8, "height": 8},
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"

    # 400 input_error: traversal-style pyramid id is rejected, not followed.
    resp = _npy_region(client, "bad..id", 0, 0, 0, 4, 4)
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"

    # 400 input_error: level count beyond the 1x1 maximum.
    resp = client.post("/pyramids", json={
        "source": {"kind": "synthetic", "pattern": "gradient",
                   "width": 8, "height": 8},
        "levels": 99,
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"


def test_malformed_npy_source_via_api(client, tmp_path):
    bad = tmp_path / "broken.npy"
    bad.write_bytes(b"not a numpy file at all")
    resp = client.post("/pyramids", json={
        "source": {"kind": "npy", "path": str(bad)},
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "input_error"

    # 507 resource_exhausted: fixture limit is 4096 px; a 256x256 level-0
    # region asks for 65536 px.
    _build(client, pid="huge", pattern="gradient", w=256, h=256, levels=1)
    resp = _npy_region(client, "huge", 0, 0, 0, 256, 256)  # 65536 > 4096
    assert resp.status_code == 507
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_corrupt_tile_surfaces_compute_failure_not_black_pixels(client, tmp_path):
    _build(client, pid="corrupt", pattern="gradient", w=32, h=32,
           tile_size=16, levels=2)
    tile = tmp_path / "api_store" / "corrupt" / "levels" / "1" / "tiles" / "0_0.npy"
    assert tile.exists()
    tile.write_bytes(b"\x00" * 128)  # garbage, and not even a valid .npy

    resp = _npy_region(client, "corrupt", 1, 0, 0, 8, 8)
    assert resp.status_code == 500
    assert resp.json()["error"]["category"] == "compute_failure"

    report = client.get("/pyramids/corrupt/validate", params={"level": 1})
    assert report.status_code == 200
    body = report.json()
    assert body["ok"] is False
    assert body["levels"][0]["bad"][0]["tx"] == 0


def test_validate_ok_on_healthy_pyramid(client):
    _build(client, pid="ok", w=32, h=32, levels=2)
    body = client.get("/pyramids/ok/validate").json()
    assert body["ok"] is True
    assert [r["level"] for r in body["levels"]] == [0, 1]
