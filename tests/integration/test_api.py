"""Integration tests for the HTTP API (FastAPI TestClient)."""

import base64
import io
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from graphcut.config import Settings
from graphcut.main import create_app


@pytest.fixture
def client():
    with TestClient(create_app(Settings())) as c:
        yield c


def hand_case_request():
    """The 1x2 hand-computed case: optimum energy 1.3 at labels [[0, 1]]."""
    return {
        "unaries": {"unary0": [[0.2, 3.0]], "unary1": [[2.0, 0.1]]},
        "pairwise": {"type": "potts", "weight": 1.0},
        "seeds": [],
    }


class TestSegmentEndpoint:
    def test_hand_computed_case(self, client, test_log):
        resp = client.post("/v1/segment", json=hand_case_request())
        assert resp.status_code == 200
        body = resp.json()
        test_log.info("api.segment run_id=%s energy=%s",
                      body["run_id"], body["energy"]["total"])
        assert body["labels"] == [[0, 1]]
        assert body["energy"]["total"] == pytest.approx(1.3)
        assert body["energy"]["data"] + body["energy"]["smooth"] == \
            pytest.approx(body["energy"]["total"])
        cert = body["certificate"]
        assert cert["verified"] is True
        # Raw flow is arcs-only; flow + graph_constant == energy total.
        assert cert["flow_value"] == pytest.approx(1.0)
        assert cert["flow_value"] + cert["graph_constant"] == \
            pytest.approx(body["energy"]["total"])
        assert cert["abs_gap_flow_cut"] <= cert["tolerance"]
        assert cert["abs_gap_cut_energy"] <= cert["tolerance"]
        assert body["run_id"]

    def test_non_submodular_rejected_400(self, client):
        req = hand_case_request()
        req["pairwise"] = {"type": "table", "v00": 0.0, "v01": 1.0,
                           "v10": 1.0, "v11": 5.0}
        resp = client.post("/v1/segment", json=req)
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["category"] == "INPUT_VALIDATION"
        assert err["code"] == "NON_SUBMODULAR_POTENTIAL"
        assert err["run_id"]

    def test_negative_weight_rejected_400(self, client):
        req = hand_case_request()
        req["pairwise"] = {"type": "potts", "weight": -2.0}
        resp = client.post("/v1/segment", json=req)
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "NEGATIVE_SMOOTHNESS_TERM"

    def test_conflicting_seeds_rejected_409(self, client):
        req = hand_case_request()
        req["seeds"] = [{"row": 0, "col": 0, "label": 0},
                        {"row": 0, "col": 0, "label": 1}]
        resp = client.post("/v1/segment", json=req)
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["category"] == "STATE_CONFLICT"
        assert err["code"] == "SEED_CONFLICT"

    def test_image_too_large_413(self):
        app = create_app(Settings(max_pixels=4))
        with TestClient(app) as client:
            resp = client.post("/v1/segment", json={
                "unaries": {"unary0": [[0.0] * 3] * 3,
                            "unary1": [[0.0] * 3] * 3},
            })
        assert resp.status_code == 413
        err = resp.json()["error"]
        assert err["category"] == "RESOURCE_EXHAUSTED"
        assert err["code"] == "IMAGE_TOO_LARGE"

    def test_missing_input_400(self, client):
        resp = client.post("/v1/segment", json={})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "MISSING_INPUT"


class TestValidateEndpoint:
    def test_valid_request_report(self, client):
        resp = client.post("/v1/validate", json=hand_case_request())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        names = [c["check"] for c in body["checks"]]
        assert "input_contract" in names and "graph_feasible" in names
        assert body["summary"]["big_m"] == 0.0  # no seeds

    def test_invalid_request_report_not_exception(self, client):
        req = hand_case_request()
        req["pairwise"] = {"type": "table", "v00": 0.0, "v01": 1.0,
                           "v10": 1.0, "v11": 9.0}
        resp = client.post("/v1/validate", json=req)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["checks"][0]["ok"] is False
        assert "non_submodular" in body["checks"][0]["check"]


class TestPngRoundTrip:
    def test_png_base64_image(self, client):
        arr = np.zeros((8, 8), dtype=np.uint8)
        arr[2:6, 2:6] = 255  # bright square = foreground
        buf = io.BytesIO()
        Image.fromarray(arr, mode="L").save(buf, format="PNG")
        payload = base64.b64encode(buf.getvalue()).decode("ascii")
        resp = client.post("/v1/segment", json={
            "image": {"format": "png_base64", "data": payload},
            "data_model": {"type": "intensity_quadratic",
                           "fg_mean": 255.0, "bg_mean": 0.0, "sigma": 40.0},
            "pairwise": {"type": "potts", "weight": 2.0},
            "seeds": [{"row": 3, "col": 3, "label": 1},
                      {"row": 0, "col": 0, "label": 0}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["certificate"]["verified"] is True
        assert body["labels"][3][3] == 1
        assert body["labels"][0][0] == 0

    def test_bad_base64_400(self, client):
        resp = client.post("/v1/segment", json={
            "image": {"format": "png_base64", "data": "!!!not-base64!!!"},
        })
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "IMAGE_DECODE_FAILED"


class TestJobsApi:
    def test_job_lifecycle_over_http(self, client, test_log):
        resp = client.post("/v1/jobs", json=hand_case_request())
        assert resp.status_code == 202
        job = resp.json()
        job_id = job["job_id"]
        assert job["state"] in ("PENDING", "RUNNING")

        final = None
        for _ in range(200):
            view = client.get(f"/v1/jobs/{job_id}").json()
            if view["state"] in ("SUCCEEDED", "FAILED", "CANCELED"):
                final = view
                break
            time.sleep(0.02)
        assert final is not None, "job did not finish"
        test_log.info("api.job job_id=%s state=%s run_id=%s",
                      job_id, final["state"], final["run_id"])
        assert final["state"] == "SUCCEEDED"

        result = client.get(f"/v1/jobs/{job_id}/result")
        assert result.status_code == 200
        assert result.json()["certificate"]["verified"] is True
        assert result.json()["energy"]["total"] == pytest.approx(1.3)

        # Cancelling a finished job is a state conflict.
        resp = client.post(f"/v1/jobs/{job_id}/cancel")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "JOB_ALREADY_FINISHED"

    def test_unknown_job_404(self, client):
        resp = client.get("/v1/jobs/job-424242")
        assert resp.status_code == 404
        assert resp.json()["error"]["category"] == "NOT_FOUND"


class TestHealth:
    def test_health(self, client):
        assert client.get("/health").json() == {"status": "ok"}
