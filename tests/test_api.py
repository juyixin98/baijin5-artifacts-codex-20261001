"""API tests via FastAPI TestClient: status codes, error categories, payloads."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fir_backend.api import create_app
from fir_backend.fixtures import make_fixture
from fir_backend.runlog import RunLogger


@pytest.fixture()
def client():
    return TestClient(create_app())


def _body(fixture, **overrides):
    body = {
        "excitation": fixture.excitation.tolist(),
        "response": fixture.response.tolist(),
        "model_order": 8,
        "delay": 0,
        "regularization": 0.0,
        "holdout_fraction": 0.25,
    }
    body.update(overrides)
    return body


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_one_shot_estimate_recovers_known_fir(client):
    fixture = make_fixture("noisy", noise_std=0.05)
    response = client.post("/v1/estimate", json=_body(fixture))
    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"]
    coefficients = np.array(payload["coefficients"])
    assert np.max(np.abs(coefficients - fixture.true_coefficients)) < 0.05
    diag = payload["diagnostics"]
    assert diag["identifiable"] is True
    assert diag["rank"] == 8
    assert diag["train_rmse"] > 0
    assert diag["holdout_rmse"] > 0
    assert diag["train_rmse"] != diag["holdout_rmse"]


def test_one_shot_narrowband_reports_unidentifiable(client):
    fixture = make_fixture("narrowband")
    response = client.post("/v1/estimate", json=_body(fixture))
    assert response.status_code == 200
    diag = response.json()["diagnostics"]
    assert diag["identifiable"] is False
    assert diag["effective_rank"] < 8
    assert len(diag["unidentifiable_reasons"]) > 0


def test_one_shot_delayed_fixture_with_explicit_delay(client):
    fixture = make_fixture("delayed", delay=5)
    response = client.post("/v1/estimate", json=_body(fixture, delay=5))
    assert response.status_code == 200
    coefficients = np.array(response.json()["coefficients"])
    np.testing.assert_allclose(coefficients, fixture.true_coefficients, atol=1e-8)


def test_mismatched_lengths_map_to_400_input_error(client):
    fixture = make_fixture("clean")
    body = _body(fixture)
    body["response"] = body["response"][:-1]
    response = client.post("/v1/estimate", json=body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["category"] == "input_error"
    assert error["run_id"]


def test_schema_violation_maps_to_400(client):
    fixture = make_fixture("clean")
    response = client.post("/v1/estimate", json=_body(fixture, model_order=0))
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"


def test_nan_input_maps_to_400(client):
    fixture = make_fixture("clean")
    body = _body(fixture)
    body["excitation"][3] = float("nan")
    # httpx's json= rejects NaN, so serialize with the stdlib encoder
    # (which emits the NaN literal) and post the raw payload.
    import json as stdlib_json

    response = client.post(
        "/v1/estimate",
        content=stdlib_json.dumps(body),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["category"] == "input_error"


def test_sample_limit_maps_to_413():
    client = TestClient(create_app(max_samples=64))
    fixture = make_fixture("clean", n_samples=256)
    response = client.post("/v1/estimate", json=_body(fixture))
    assert response.status_code == 413
    assert response.json()["error"]["category"] == "resource_exhausted"


def test_stream_round_trip_via_api(client):
    fixture = make_fixture("clean", n_samples=256)
    created = client.post("/v1/streams")
    assert created.status_code == 201
    stream_id = created.json()["stream_id"]

    half = fixture.excitation.size // 2
    for lo, hi in ((0, half), (half, fixture.excitation.size)):
        appended = client.post(
            f"/v1/streams/{stream_id}/blocks",
            json={
                "excitation": fixture.excitation[lo:hi].tolist(),
                "response": fixture.response[lo:hi].tolist(),
            },
        )
        assert appended.status_code == 200
    assert appended.json()["n_samples"] == 256

    sealed = client.post(f"/v1/streams/{stream_id}/seal")
    assert sealed.status_code == 200
    assert sealed.json()["state"] == "sealed"

    estimated = client.post(
        f"/v1/streams/{stream_id}/estimate", json={"model_order": 8}
    )
    assert estimated.status_code == 200
    coefficients = np.array(estimated.json()["coefficients"])
    np.testing.assert_allclose(coefficients, fixture.true_coefficients, atol=1e-8)


def test_stream_state_conflict_maps_to_409(client):
    stream_id = client.post("/v1/streams").json()["stream_id"]
    block = {"excitation": [1.0] * 16, "response": [1.0] * 16}
    client.post(f"/v1/streams/{stream_id}/blocks", json=block)
    client.post(f"/v1/streams/{stream_id}/seal")
    response = client.post(f"/v1/streams/{stream_id}/blocks", json=block)
    assert response.status_code == 409
    assert response.json()["error"]["category"] == "state_conflict"


def test_unknown_stream_maps_to_404(client):
    response = client.post(
        "/v1/streams/no-such-stream/blocks",
        json={"excitation": [1.0], "response": [1.0]},
    )
    assert response.status_code == 404


def test_failures_are_logged_with_category(client):
    logger = RunLogger()
    client = TestClient(create_app(logger=logger))
    fixture = make_fixture("clean")
    body = _body(fixture)
    body["response"] = body["response"][:-1]
    client.post("/v1/estimate", json=body)
    failure_events = [r for r in logger.records if r.event == "request_failed"]
    assert failure_events
    assert failure_events[0].payload["category"] == "input_error"
