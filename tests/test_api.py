"""API tests: full job lifecycle over HTTP plus error-category mapping."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from tileconv.api import create_app


@pytest.fixture()
def client(settings):
    return TestClient(create_app(settings))


def make_image(client, kind="random", shape=(40, 34), seed=3):
    resp = client.post("/fixtures/images",
                       json={"kind": kind, "shape": list(shape), "seed": seed})
    assert resp.status_code == 201, resp.text
    return resp.json()["image_id"]


def make_kernel(client):
    resp = client.post("/kernels", json={
        "kind": "dense",
        "weights": [[1.0, 2.0, 1.0], [2.0, 4.0, 2.0], [1.0, 2.0, 1.0]],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["kernel_id"]


def make_job(client, image_id, kernel_id, tile=(16, 16), boundary="mirror"):
    resp = client.post("/jobs", json={
        "image_id": image_id, "kernel_id": kernel_id,
        "tile_shape": list(tile), "boundary": boundary, "cval": 0.0,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["job_id"]


class TestLifecycle:
    def test_full_flow_run_validate(self, client):
        image_id = make_image(client)
        kernel_id = make_kernel(client)
        job_id = make_job(client, image_id, kernel_id)

        resp = client.post(f"/jobs/{job_id}/run", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

        status = client.get(f"/jobs/{job_id}").json()
        assert status["progress"] == {"done": 9, "total": 9, "fraction": 1.0}
        assert "numpy" in status["versions"]

        report = client.post(f"/jobs/{job_id}/validate", json={}).json()
        assert report["passed"] is True
        assert report["mismatch_count"] == 0
        assert "basis" in report and "scipy" in report["reference"]
        assert report["context"]["image_digest"]

        result = client.get(f"/jobs/{job_id}/result").json()
        assert result["shape"] == [40, 34]
        assert result["output_digest"]

    def test_interrupt_resume_validate_flow(self, client):
        image_id = make_image(client, shape=(48, 40))
        kernel_id = make_kernel(client)
        job_id = make_job(client, image_id, kernel_id)

        resp = client.post(f"/jobs/{job_id}/run", json={"fail_after": 2})
        assert resp.status_code == 500
        assert resp.json()["error"]["category"] == "InterruptInjected"

        status = client.get(f"/jobs/{job_id}").json()
        assert status["status"] == "interrupted"
        assert status["progress"]["done"] == 2

        resp = client.post(f"/jobs/{job_id}/resume", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

        report = client.post(f"/jobs/{job_id}/validate", json={}).json()
        assert report["passed"] is True

    def test_versions_endpoint(self, client):
        body = client.get("/meta/versions").json()
        for key in ("python", "numpy", "scipy", "pillow", "tileconv"):
            assert key in body["versions"]

    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}


class TestErrorCategories:
    def test_missing_job_is_404_not_found(self, client):
        resp = client.get("/jobs/doesnotexist")
        assert resp.status_code == 404
        assert resp.json()["error"]["category"] == "NotFound"

    def test_missing_image_is_404(self, client):
        kernel_id = make_kernel(client)
        resp = client.post("/jobs", json={
            "image_id": "nope", "kernel_id": kernel_id,
            "tile_shape": [16, 16], "boundary": "mirror", "cval": 0.0,
        })
        assert resp.status_code == 404
        assert resp.json()["error"]["category"] == "NotFound"

    def test_invalid_kernel_spec_is_422(self, client):
        resp = client.post("/kernels", json={
            "kind": "dense", "weights": [[1.0, 2.0]], "anchor": [0, 5],
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "InvalidSpec"

    def test_invalid_boundary_is_422(self, client):
        image_id = make_image(client)
        kernel_id = make_kernel(client)
        resp = client.post("/jobs", json={
            "image_id": image_id, "kernel_id": kernel_id,
            "tile_shape": [16, 16], "boundary": "clamp-to-edge", "cval": 0.0,
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "InvalidSpec"

    def test_validate_before_completion_is_409(self, client):
        image_id = make_image(client)
        kernel_id = make_kernel(client)
        job_id = make_job(client, image_id, kernel_id)
        resp = client.post(f"/jobs/{job_id}/validate", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["category"] == "JobStateError"

    def test_resume_after_input_tamper_is_409_digest_mismatch(self, client, settings):
        image_id = make_image(client)
        kernel_id = make_kernel(client)
        job_id = make_job(client, image_id, kernel_id)
        resp = client.post(f"/jobs/{job_id}/run", json={"fail_after": 1})
        assert resp.status_code == 500

        # corrupt the stored image
        from tileconv.storage import ImageStore

        store = ImageStore(settings.workspace)
        img = store.open_array(image_id, writable=True)
        img[0, 0] += 5.0
        img.flush()

        resp = client.post(f"/jobs/{job_id}/resume", json={})
        assert resp.status_code == 409
        assert resp.json()["error"]["category"] == "DigestMismatch"

    def test_unknown_route_is_404_not_success(self, client):
        resp = client.get("/no/such/route")
        assert resp.status_code == 404
