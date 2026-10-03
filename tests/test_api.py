"""End-to-end API tests via FastAPI TestClient, including error taxonomy
(input / state conflict / resource exhausted / computation failure) and
run-log replay evidence."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import AppConfig
from app.main import create_app

from .fixtures import KNOWN_FIR, make_response, narrowband_excitation, white_excitation

ORDER = len(KNOWN_FIR)


@pytest.fixture()
def client(tmp_path):
    config = AppConfig(
        max_samples=4096,
        max_order=64,
        max_delay=256,
        max_matrix_cells=4096 * 64,
        default_regularization=1e-6,
        log_dir=str(tmp_path / "logs"),
    )
    with TestClient(create_app(config)) as c:
        c.log_dir = Path(tmp_path / "logs")
        yield c


def _payload(x, y, **overrides):
    body = {
        "excitation": x.tolist(),
        "response": y.tolist(),
        "model_order": ORDER,
        "regularization": 1e-8,
        "holdout_fraction": 0.25,
    }
    body.update(overrides)
    return body


def test_estimate_happy_path_recovers_channel(client):
    x = white_excitation(2000, seed=51)
    y = make_response(x, KNOWN_FIR)
    resp = client.post("/v1/fir/estimate", json=_payload(x, y))
    assert resp.status_code == 200
    body = resp.json()
    np.testing.assert_allclose(body["coefficients"], KNOWN_FIR, atol=1e-4)
    assert body["identifiable"] is True
    assert body["train_metrics"]["mse"] < 1e-12
    assert body["holdout_metrics"]["mse"] < 1e-12
    assert body["run_id"]
    assert resp.headers["X-Run-Id"] == body["run_id"]


def test_estimate_with_estimated_delay(client):
    x = white_excitation(2000, seed=52)
    y = make_response(x, KNOWN_FIR, delay=6)
    resp = client.post(
        "/v1/fir/estimate", json=_payload(x, y, estimate_delay=True)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["delay"] == 6
    assert body["delay_source"] == "estimated_cross_correlation"
    np.testing.assert_allclose(body["coefficients"], KNOWN_FIR, atol=1e-4)


def test_identifiability_failure_is_distinct_422(client):
    x = narrowband_excitation(1500, frequency=0.05, n_tones=1)
    y = make_response(x, KNOWN_FIR)
    resp = client.post(
        "/v1/fir/estimate",
        json=_payload(x, y, regularization=1e-4, require_identifiable=True),
    )
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "IDENTIFIABILITY_FAILURE"
    assert error["reason"] == "rank_deficient_excitation"
    assert error["detail"]["rank"] == 2


def test_unregularized_rank_deficient_is_computation_failure(client):
    x = narrowband_excitation(1500, frequency=0.05, n_tones=1)
    y = make_response(x, KNOWN_FIR)
    resp = client.post(
        "/v1/fir/estimate", json=_payload(x, y, regularization=0.0)
    )
    assert resp.status_code == 500
    error = resp.json()["error"]
    assert error["code"] == "COMPUTATION_FAILURE"
    assert error["reason"] == "normal_equations_singular"


def test_input_validation_error_is_distinct_400(client):
    x = white_excitation(300, seed=53)
    resp = client.post(
        "/v1/fir/estimate", json=_payload(x, x.copy())  # identical signals
    )
    assert resp.status_code == 400
    error = resp.json()["error"]
    assert error["code"] == "INPUT_VALIDATION"
    assert error["reason"] == "excitation_equals_response"


def test_schema_validation_error_is_422(client):
    resp = client.post("/v1/fir/estimate", json={"excitation": [1.0]})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INPUT_VALIDATION"


def test_resource_exhausted_is_distinct_413(client):
    x = white_excitation(5000, seed=54)  # > max_samples=4096 in test config
    y = make_response(x, KNOWN_FIR)
    resp = client.post("/v1/fir/estimate", json=_payload(x, y))
    assert resp.status_code == 413
    error = resp.json()["error"]
    assert error["code"] == "RESOURCE_EXHAUSTED"
    assert error["reason"] == "too_many_samples"


def test_stream_session_flow_and_state_conflicts(client):
    x = white_excitation(1200, seed=55)
    y = make_response(x, KNOWN_FIR)
    created = client.post("/v1/sessions")
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    half = 600
    r1 = client.post(
        f"/v1/sessions/{session_id}/chunks",
        json={"excitation": x[:half].tolist(), "response": y[:half].tolist()},
    )
    assert r1.status_code == 200
    assert r1.json()["n_samples"] == half
    r2 = client.post(
        f"/v1/sessions/{session_id}/chunks",
        json={"excitation": x[half:].tolist(), "response": y[half:].tolist()},
    )
    assert r2.json()["n_chunks"] == 2

    fin = client.post(
        f"/v1/sessions/{session_id}/finalize",
        json={"model_order": ORDER, "regularization": 1e-8},
    )
    assert fin.status_code == 200
    np.testing.assert_allclose(fin.json()["coefficients"], KNOWN_FIR, atol=1e-4)

    # state conflicts are distinguishable 409s
    again = client.post(
        f"/v1/sessions/{session_id}/finalize", json={"model_order": ORDER}
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "STATE_CONFLICT"
    assert again.json()["error"]["reason"] == "already_finalized"

    append = client.post(
        f"/v1/sessions/{session_id}/chunks",
        json={"excitation": [1.0], "response": [1.0]},
    )
    assert append.status_code == 409
    assert append.json()["error"]["reason"] == "session_finalized"

    missing = client.post("/v1/sessions/nope/finalize", json={"model_order": ORDER})
    assert missing.status_code == 409
    assert missing.json()["error"]["reason"] == "session_not_found"


def test_run_log_records_replay_evidence(client):
    x = white_excitation(1500, seed=56)
    y = make_response(x, KNOWN_FIR)
    resp = client.post("/v1/fir/estimate", json=_payload(x, y))
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    log_file = client.log_dir / "runs.jsonl"
    assert log_file.exists()
    records = [
        json.loads(line)
        for line in log_file.read_text().splitlines()
        if json.loads(line)["run_id"] == run_id
    ]
    events = [r["event"] for r in records]
    # key intermediate states and decision reasons are preserved for replay
    for expected in (
        "estimate_requested",
        "input_validated",
        "aligned",
        "design_matrix_built",
        "split",
        "identifiability_assessed",
        "solved",
        "metrics_computed",
        "estimate_completed",
    ):
        assert expected in events, f"missing event {expected}"
    ident = next(r for r in records if r["event"] == "identifiability_assessed")
    assert ident["rank"] == ORDER
    assert "reason" in ident
    matrix = next(r for r in records if r["event"] == "design_matrix_built")
    assert matrix["n_rows"] == 1500 - ORDER + 1
    assert matrix["n_columns"] == ORDER
