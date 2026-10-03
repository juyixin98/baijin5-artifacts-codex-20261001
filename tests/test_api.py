"""API tests: endpoints, error categories, validation verdicts."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Settings


@pytest.fixture()
def client(tmp_path):
    settings = Settings(workspace_dir=tmp_path / "ws", log_dir=tmp_path / "logs")
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


JOB_BODY = {
    "image": {"kind": "noise", "shape": [48, 40], "seed": 3},
    "kernel": {"kind": "gaussian", "k": 5, "sigma": 1.1},
    "boundary": "mirror",
    "cval": 0.0,
    "tile": [16, 16],
}


class TestHealth:
    def test_health_reports_versions(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert {"python", "numpy", "scipy"} <= set(body["versions"])


class TestJobEndpoints:
    def test_create_run_status_report(self, client):
        created = client.post("/jobs", json=JOB_BODY).json()
        job_id = created["job_id"]
        assert created["status"] == "pending"
        assert created["tiles_total"] == 9  # 3x3 tiles over 48x40 @16

        run = client.post(f"/jobs/{job_id}/run").json()
        assert run["status"] == "completed"
        assert run["progress"] == {"done": 9, "total": 9}

        status = client.get(f"/jobs/{job_id}").json()
        assert status["status"] == "completed"

        report = client.get(f"/jobs/{job_id}/report").json()
        assert report["status"] == "completed"
        assert report["peak_rss_bytes"] > 0

    def test_interrupt_and_resume_via_api(self, client):
        job_id = client.post("/jobs", json=JOB_BODY).json()["job_id"]
        interrupted = client.post(f"/jobs/{job_id}/run",
                                  json={"max_tiles": 2}).json()
        assert interrupted["status"] == "interrupted"
        assert interrupted["progress"]["done"] == 2
        resumed = client.post(f"/jobs/{job_id}/resume").json()
        assert resumed["status"] == "completed"

    def test_unknown_job_404(self, client):
        resp = client.get("/jobs/nope")
        assert resp.status_code == 404
        assert resp.json()["category"] == "unknown_job"

    def test_invalid_spec_422(self, client):
        bad = dict(JOB_BODY, boundary="mirrorish")
        resp = client.post("/jobs", json=bad)
        assert resp.status_code == 422
        assert resp.json()["category"] == "invalid_spec"

        bad2 = dict(JOB_BODY, kernel={"kind": "dense",
                                      "weights": [[1.0, 2.0]],
                                      "anchor": [5, 0]})
        resp2 = client.post("/jobs", json=bad2)
        assert resp2.status_code == 422

    def test_resume_with_tampered_kernel_409(self, client, tmp_path):
        job_id = client.post("/jobs", json=JOB_BODY).json()["job_id"]
        client.post(f"/jobs/{job_id}/run", json={"max_tiles": 1})
        manifest_path = (tmp_path / "ws" / "jobs" / job_id / "manifest.json")
        manifest = json.loads(manifest_path.read_text())
        manifest["spec"]["kernel"] = {"kind": "box", "k": 3}
        manifest_path.write_text(json.dumps(manifest))
        resp = client.post(f"/jobs/{job_id}/resume")
        assert resp.status_code == 409
        assert resp.json()["category"] == "digest_mismatch"


class TestValidateEndpoint:
    def test_validate_pass_with_basis(self, client):
        body = client.post("/validate", json=JOB_BODY).json()
        assert body["status"] == "pass"
        errs = body["errors"]
        assert errs["tiled_vs_direct"] <= body["tolerance"]
        assert errs["tiled_vs_scipy"] <= body["tolerance"]
        assert errs["direct_vs_scipy"] <= body["tolerance"]
        assert "basis" in body

    def test_validate_separable(self, client):
        body = client.post("/validate", json=dict(
            JOB_BODY, kernel={"kind": "separable_gaussian", "k": 7, "sigma": 1.5}
        )).json()
        assert body["status"] == "pass"

    def test_validate_even_kernel_all_boundaries(self, client):
        for boundary in ("mirror", "constant", "periodic"):
            body = client.post("/validate", json=dict(
                JOB_BODY,
                boundary=boundary,
                cval=0.4,
                kernel={"kind": "dense",
                        "weights": [[0.1, 0.2, 0.3, 0.4]] * 4,
                        "anchor": [2, 2]},
            )).json()
            assert body["status"] == "pass", body

    def test_validate_rejects_bad_boundary_422(self, client):
        resp = client.post("/validate", json=dict(JOB_BODY, boundary="nope"))
        assert resp.status_code == 422
        assert resp.json()["category"] == "invalid_spec"
