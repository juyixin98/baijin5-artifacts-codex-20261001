"""End-to-end HTTP contract tests via FastAPI TestClient."""

from __future__ import annotations

import numpy as np


def create_stream(client, stream_id="s1", **overrides):
    payload = {
        "stream_id": stream_id,
        "algorithm": "nlms",
        "filter_length": 8,
        "mu": 0.5,
        "channels": ["a", "b"],
    }
    payload.update(overrides)
    return client.post("/v1/streams", json=payload)


class TestStreamLifecycle:
    def test_healthz(self, client):
        assert client.get("/healthz").json() == {"status": "ok"}

    def test_create_and_inspect(self, client):
        resp = create_stream(client)
        assert resp.status_code == 201
        assert resp.json()["run_id"]
        state = client.get("/v1/streams/s1/channels/a/state").json()
        assert state["next_index"] == 0
        assert state["weights"] == [0.0] * 8
        assert state["algorithm"] == "nlms"

    def test_duplicate_stream_conflict(self, client):
        create_stream(client)
        resp = create_stream(client)
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["kind"] == "state_conflict"
        assert body["reason"] == "stream_exists"
        assert body["run_id"]

    def test_unknown_stream_404(self, client):
        resp = client.get("/v1/streams/ghost/channels/a/state")
        assert resp.status_code == 404
        assert resp.json()["error"]["reason"] == "unknown_stream"

    def test_delete_stream(self, client):
        create_stream(client)
        assert client.delete("/v1/streams/s1").status_code == 204
        assert client.get("/v1/streams/s1/channels/a/state").status_code == 404


class TestBlockProcessing:
    def test_block_roundtrip(self, client):
        create_stream(client)
        rng = np.random.default_rng(0)
        payload = {
            "start_index": 0,
            "reference": rng.standard_normal(32).tolist(),
            "desired": rng.standard_normal(32).tolist(),
        }
        resp = client.post("/v1/streams/s1/channels/a/blocks", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["end_index"] == 32
        assert body["samples_processed"] == 32
        assert body["frozen_samples"] == 0
        assert len(body["outputs"]) == len(body["errors"]) == 32
        # Channel b untouched.
        assert client.get("/v1/streams/s1/channels/b/state").json()["next_index"] == 0

    def test_index_mismatch_conflict(self, client):
        create_stream(client)
        payload = {"start_index": 5, "reference": [0.0], "desired": [0.0]}
        resp = client.post("/v1/streams/s1/channels/a/blocks", json=payload)
        assert resp.status_code == 409
        body = resp.json()["error"]
        assert body["kind"] == "state_conflict"
        assert body["reason"] == "index_mismatch"
        assert body["detail"]["expected"] == 0

    def test_freeze_adaptation_via_api(self, client):
        create_stream(client, frozen_until_index=2)
        rng = np.random.default_rng(1)
        payload = {
            "start_index": 0,
            "reference": rng.standard_normal(8).tolist(),
            "desired": rng.standard_normal(8).tolist(),
        }
        body = client.post("/v1/streams/s1/channels/a/blocks", json=payload).json()
        assert body["frozen_samples"] == 2


class TestErrorContract:
    def test_mu_out_of_range(self, client):
        resp = create_stream(client, mu=2.0)  # NLMS upper bound is exclusive
        assert resp.status_code == 422
        body = resp.json()["error"]
        assert body["kind"] == "input_validation"
        assert body["reason"] == "mu_range"

    def test_schema_violation(self, client):
        resp = client.post("/v1/streams", json={"stream_id": "x"})
        assert resp.status_code == 422
        body = resp.json()["error"]
        assert body["kind"] == "input_validation"
        assert body["reason"] == "schema_violation"

    def test_non_finite_block_rejected(self, client):
        create_stream(client)
        # Strict JSON cannot carry NaN, so send the raw body a lenient
        # client would produce; the server must still reject it cleanly.
        resp = client.post(
            "/v1/streams/s1/channels/a/blocks",
            content=b'{"start_index": 0, "reference": [1.0, NaN],'
                    b' "desired": [1.0, 1.0]}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["reason"] == "non_finite_input"

    def test_block_too_long(self, client, settings):
        # Rebuild the app with a tiny block limit.
        from fastapi.testclient import TestClient

        from app.config import Settings
        from app.main import create_app

        small = TestClient(create_app(Settings(
            max_block_length=4, log_dir=settings.log_dir
        )))
        create_stream(small)
        payload = {"start_index": 0, "reference": [0.0] * 5, "desired": [0.0] * 5}
        resp = small.post("/v1/streams/s1/channels/a/blocks", json=payload)
        assert resp.status_code == 413
        assert resp.json()["error"]["kind"] == "resource_exhausted"

    def test_computation_failure_via_api(self, client):
        create_stream(client, algorithm="lms", mu=1.0)
        payload = {"start_index": 0, "reference": [1e200] * 32,
                   "desired": [1.0] * 32}
        resp = client.post("/v1/streams/s1/channels/a/blocks", json=payload)
        assert resp.status_code == 500
        body = resp.json()["error"]
        assert body["kind"] == "computation_failure"
        assert body["reason"] == "non_finite_state"
        # Rolled back: same index still accepted.
        ok = client.post("/v1/streams/s1/channels/a/blocks", json={
            "start_index": 0, "reference": [1.0], "desired": [1.0],
        })
        assert ok.status_code == 200


class TestEvaluate:
    def test_success_against_clean(self, client):
        rng = np.random.default_rng(4)
        clean = rng.standard_normal(100)
        noise = rng.standard_normal(100)
        desired = clean + noise
        residual = clean + 0.05 * noise  # 26 dB noise reduction
        resp = client.post("/v1/evaluate", json={
            "desired": desired.tolist(),
            "residual": residual.tolist(),
            "clean": clean.tolist(),
        })
        body = resp.json()
        assert body["success"] is True
        assert body["noise_reduction_db"] > 20.0
        assert "output-energy" in body["rationale"]

    def test_no_noise_is_input_error_not_success(self, client):
        clean = [0.1] * 16
        resp = client.post("/v1/evaluate", json={
            "desired": clean, "residual": clean, "clean": clean,
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["reason"] == "no_noise_present"
