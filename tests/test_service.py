"""服务端到端测试：区域查询数值、跨瓦片拼接、错误分类与 run_id。"""
from __future__ import annotations

import io

import numpy as np

from pyramid_service.contracts import GeneratorSpec
from pyramid_service.synth import generate_window

from .reference import ref_pyramid


def _create(client, **overrides):
    body = {
        "generator": "gradient",
        "width": 17,
        "height": 9,
        "levels": 3,
        "params": {"kx": 3, "ky": 5},
    }
    body.update(overrides)
    resp = client.post("/images", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _reference_levels(spec: GeneratorSpec, n_levels: int):
    full = generate_window(spec, 0, 0, spec.height, spec.width).astype(np.float64)
    return ref_pyramid(full, n_levels)


def test_create_and_region_matches_reference(client):
    created = _create(client)
    image_id = created["image"]["image_id"]
    assert created["run_id"].startswith("run-")
    assert [(l["height"], l["width"]) for l in created["image"]["levels"]] == [
        (9, 17),
        (5, 9),
        (3, 5),
    ]
    spec = GeneratorSpec(kind="gradient", width=17, height=9, params={"kx": 3, "ky": 5})
    refs = _reference_levels(spec, 3)

    # 跨瓦片区域（tile_size=4，level 1 为 9x5；x=3..8 跨 3 列瓦片，y=1..4 跨 2 行瓦片）
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 1, "x": 3, "y": 1, "w": 6, "h": 4, "format": "json"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    got = np.array(payload["data"], dtype=np.float64)
    np.testing.assert_allclose(got, refs[1][1:5, 3:9], atol=1e-5)

    # 第 0 层奇数边界单像素
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 0, "x": 16, "y": 8, "w": 1, "h": 1},
    )
    assert resp.status_code == 200
    assert resp.json()["data"][0][0] == refs[0][8, 16]


def test_region_npy_roundtrip(client):
    image_id = _create(client)["image"]["image_id"]
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 0, "x": 0, "y": 0, "w": 17, "h": 9, "format": "npy"},
    )
    assert resp.status_code == 200
    arr = np.load(io.BytesIO(resp.content))
    spec = GeneratorSpec(kind="gradient", width=17, height=9, params={"kx": 3, "ky": 5})
    np.testing.assert_array_equal(arr, generate_window(spec, 0, 0, 9, 17))


def test_missing_level_is_state_conflict_404(client):
    image_id = _create(client)["image"]["image_id"]
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 7, "x": 0, "y": 0, "w": 1, "h": 1},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["category"] == "state_conflict"
    assert body["error"]["run_id"]


def test_out_of_bounds_region_is_input_error(client):
    image_id = _create(client)["image"]["image_id"]
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 0, "x": 10, "y": 0, "w": 100, "h": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_oversize_region_is_resource_exhausted(client, settings):
    # settings.max_region_pixels = 4096；17x9 图最大层 0 全图 153 像素，
    # 造一张更大的图触发上限
    created = _create(client, generator="checkerboard", width=200, height=200,
                      levels=1, params={"period": 4})
    image_id = created["image"]["image_id"]
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 0, "x": 0, "y": 0, "w": 200, "h": 200},
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_unknown_generator_is_input_error(client):
    resp = client.post(
        "/images", json={"generator": "plasma", "width": 8, "height": 8}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_oversize_image_is_resource_exhausted(client):
    resp = client.post(
        "/images", json={"generator": "gradient", "width": 2000, "height": 2000}
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_unknown_image_is_404(client):
    resp = client.get("/images/img-does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "state_conflict"


def test_corrupt_tile_is_compute_failure_not_black(client, store):
    image_id = _create(client)["image"]["image_id"]
    tile_path = store.level_dir(image_id, 0) / "tiles" / "0000_0000.npy"
    data = bytearray(tile_path.read_bytes())
    data[-4] ^= 0xFF
    tile_path.write_bytes(bytes(data))
    resp = client.get(
        f"/images/{image_id}/region",
        params={"level": 0, "x": 0, "y": 0, "w": 4, "h": 4},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["category"] == "compute_failure"
    assert "checksum" in body["error"]["message"]


def test_error_response_carries_run_id_header(client):
    resp = client.get("/images/img-nope")
    assert resp.headers["X-Run-Id"].startswith("run-")
    assert resp.json()["error"]["run_id"] == resp.headers["X-Run-Id"]
