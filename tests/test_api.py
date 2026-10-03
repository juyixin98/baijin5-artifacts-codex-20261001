"""Service-layer tests: endpoints, error categories, run logging."""

from __future__ import annotations

import json
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient

from limiter import LimiterConfig
from limiter.api import app


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _payload(pcm: np.ndarray, config: dict | None = None, **extra) -> dict:
    return {
        "config": config or {},
        "channels": pcm.T.tolist(),
        **extra,
    }


class TestHealth:
    def test_health_reports_versions(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        for lib in ("python", "numpy", "scipy"):
            assert lib in body["versions"]


class TestLimitEndpoint:
    def test_processes_fixture_and_reports_ceiling(
        self, client, default_config, sustained_peaks
    ):
        pcm, _ = sustained_peaks
        resp = client.post("/v1/limit", json=_payload(pcm))
        assert resp.status_code == 200
        body = resp.json()
        assert body["latency_samples"] == default_config.lookahead_samples
        out = np.asarray(body["output"])
        assert out.shape == pcm.T.shape
        assert len(body["gain"]) == pcm.shape[0]
        T = default_config.threshold_linear
        assert np.max(np.abs(out)) <= T * (1 + 1e-9), "CEILING VIOLATION via API"
        assert body["stats"]["sample_peak_ceiling_ok"] is True

    def test_run_id_echoed_and_stable(self, client, short_impulse):
        pcm, _ = short_impulse
        resp = client.post("/v1/limit", json=_payload(pcm, run_id="test-run-1"))
        assert resp.json()["run_id"] == "test-run-1"

    def test_block_size_parameter_changes_nothing(
        self, client, block_boundary_burst
    ):
        pcm, _ = block_boundary_burst
        a = client.post("/v1/limit", json=_payload(pcm)).json()
        b = client.post("/v1/limit", json=_payload(pcm, block_size=512)).json()
        assert a["output"] == b["output"]
        assert a["gain"] == b["gain"]


class TestErrorCategories:
    def test_empty_channels_400(self, client):
        resp = client.post("/v1/limit", json={"config": {}, "channels": []})
        assert resp.status_code == 400
        assert "non-empty" in resp.json()["detail"]

    def test_ragged_channels_400(self, client):
        resp = client.post(
            "/v1/limit",
            json={"config": {}, "channels": [[0.1, 0.2], [0.1]]},
        )
        assert resp.status_code == 400
        assert "ragged" in resp.json()["detail"]

    def test_invalid_config_400(self, client, short_impulse):
        pcm, _ = short_impulse
        resp = client.post(
            "/v1/limit", json=_payload(pcm, config={"attack_ms": -1.0})
        )
        assert resp.status_code == 400
        assert "attack_ms" in resp.json()["detail"]

    def test_non_finite_signal_400(self, client):
        # 1e999 parses to +inf on the server side
        resp = client.post(
            "/v1/limit",
            content='{"config": {}, "channels": [[0.0, 1e999], [0.0, 0.0]]}',
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_missing_body_422_not_success(self, client):
        resp = client.post("/v1/limit", json={})
        assert resp.status_code == 422


class TestRunLogging:
    def test_logs_carry_run_id_versions_and_input_hash(
        self, client, caplog, short_impulse
    ):
        pcm, _ = short_impulse
        with caplog.at_level(logging.INFO, logger="limiter.run"):
            resp = client.post("/v1/limit", json=_payload(pcm, run_id="log-run"))
        assert resp.status_code == 200
        records = []
        for line in caplog.messages:
            if not line.startswith("limiter "):
                continue
            records.append(json.loads(line[len("limiter ") :]))
        assert records, "no structured limiter log records captured"
        assert all(r["run_id"] == "log-run" for r in records)
        steps = {r["step"] for r in records}
        assert {"run_start", "api_request", "run_end"} <= steps
        run_start = next(r for r in records if r["step"] == "run_start")
        assert set(run_start["versions"]) == {"python", "numpy", "scipy"}
        api_req = next(r for r in records if r["step"] == "api_request")
        assert len(api_req["input_sha256"]) == 64
        run_end = next(r for r in records if r["step"] == "run_end")
        assert run_end["sample_peak_ceiling_ok"] is True
        assert (
            run_end["total_out"]
            == run_end["total_in"] + run_end["latency_samples"]
        )
