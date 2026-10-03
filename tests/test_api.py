"""End-to-end API tests via FastAPI TestClient.

Each failure category is asserted explicitly (status code + envelope
category), not just "the endpoint responded".
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from scipy.signal import butter, sosfilt

from app.main import create_app

SEED = 20261003


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture()
def lowpass_sos() -> list[list[float]]:
    return butter(4, 0.2, output="sos").tolist()


def _create(client: TestClient, sos, n_channels=2, transient="carry") -> str:
    resp = client.post("/streams", json={
        "sos": sos, "n_channels": n_channels, "transient": transient,
        "sample_rate": 48000,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()["stream_id"]


def test_create_process_roundtrip(client, lowpass_sos, run_log):
    stream_id = _create(client, lowpass_sos)
    rng = np.random.default_rng(SEED)
    x = rng.standard_normal((2, 256))
    resp = client.post(f"/streams/{stream_id}/chunks",
                       json={"samples": x.tolist()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    y = np.asarray(body["samples"])
    ref = sosfilt(np.asarray(lowpass_sos), x[0])
    err = float(np.max(np.abs(y[0] - ref)))
    run_log.log("api_roundtrip", seed=SEED, max_err=err,
                param_version=body["param_version"],
                verdict="pass" if err < 1e-10 else "fail")
    assert err < 1e-10
    assert body["param_version"] == 1
    assert body["samples_processed"] == 256


def test_chunked_api_equals_whole(client, lowpass_sos):
    rng = np.random.default_rng(SEED)
    x = rng.standard_normal((1, 300))

    whole_id = _create(client, lowpass_sos, n_channels=1)
    y_whole = np.asarray(client.post(
        f"/streams/{whole_id}/chunks", json={"samples": x.tolist()}
    ).json()["samples"])

    chunked_id = _create(client, lowpass_sos, n_channels=1)
    parts = [
        client.post(f"/streams/{chunked_id}/chunks",
                    json={"samples": x[:, i:i + 100].tolist()}).json()["samples"]
        for i in range(0, 300, 100)
    ]
    y_chunked = np.concatenate([np.asarray(p) for p in parts], axis=1)
    assert np.array_equal(y_whole, y_chunked)


def test_invalid_coefficients_422_input_error(client, run_log):
    for bad_sos, why in [
        ([[1, 0, 0, 0, 0, 0]], "zero a0"),
        ([[1, 0, 0, 1, -2.0, 0.5]], "unstable pole"),
        ([[1, 0, 0, 1, 0]], "bad shape"),
    ]:
        resp = client.post("/streams", json={"sos": bad_sos, "n_channels": 1})
        assert resp.status_code == 422, (why, resp.text)
        assert resp.json()["error"]["category"] == "input_error", why
    run_log.log("invalid_coeffs", cases=3, verdict="pass")


def test_unknown_stream_404(client):
    resp = client.post("/streams/nope/chunks", json={"samples": [[0.0]]})
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "not_found"


def test_version_conflict_409(client, lowpass_sos, run_log):
    stream_id = _create(client, lowpass_sos, n_channels=1)
    resp = client.post(f"/streams/{stream_id}/chunks",
                       json={"samples": [[1.0, 2.0]], "expected_param_version": 7})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["category"] == "state_conflict"
    assert body["error"]["detail"]["current_version"] == 1
    run_log.log("version_conflict", detail=body["error"]["detail"], verdict="pass")


def test_coefficient_update_bumps_version(client, lowpass_sos):
    stream_id = _create(client, lowpass_sos, n_channels=1)
    new_sos = butter(4, 0.3, output="sos").tolist()
    resp = client.put(f"/streams/{stream_id}/coefficients",
                      json={"sos": new_sos, "expected_param_version": 1})
    assert resp.status_code == 200, resp.text
    assert resp.json()["param_version"] == 2
    # Stale writer now conflicts.
    resp = client.put(f"/streams/{stream_id}/coefficients",
                      json={"sos": new_sos, "expected_param_version": 1})
    assert resp.status_code == 409


def test_oversize_chunk_413(client, lowpass_sos, run_log):
    stream_id = _create(client, lowpass_sos, n_channels=1)
    from app.config import MAX_CHUNK_SAMPLES

    big = np.zeros((1, MAX_CHUNK_SAMPLES + 1)).tolist()
    resp = client.post(f"/streams/{stream_id}/chunks", json={"samples": big})
    assert resp.status_code == 413
    assert resp.json()["error"]["category"] == "resource_exhausted"
    run_log.log("oversize_chunk", limit=MAX_CHUNK_SAMPLES, verdict="pass")


def test_non_finite_samples_422(client, lowpass_sos):
    stream_id = _create(client, lowpass_sos, n_channels=1)
    # httpx refuses to serialize NaN, so post a raw JSON body (Python's
    # json parser accepts the NaN literal, as does FastAPI's).
    resp = client.post(
        f"/streams/{stream_id}/chunks",
        content='{"samples": [[0.0, NaN]]}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_overflow_500_computation_failure(client, run_log):
    # Stable gain section (poles at 0); 2 * 1e308 overflows float64.
    gain_sos = [[2.0, 0.0, 0.0, 1.0, 0.0, 0.0]]
    stream_id = _create(client, gain_sos, n_channels=1)
    resp = client.post(f"/streams/{stream_id}/chunks",
                       json={"samples": [[1e308] * 16]})
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["category"] == "computation_failure"
    run_log.log("overflow_api", detail=body["error"]["detail"], verdict="pass")


def test_wrong_channel_count_422(client, lowpass_sos):
    stream_id = _create(client, lowpass_sos, n_channels=2)
    resp = client.post(f"/streams/{stream_id}/chunks",
                       json={"samples": [[1.0, 2.0]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_reset_and_delete(client, lowpass_sos):
    stream_id = _create(client, lowpass_sos, n_channels=1)
    client.post(f"/streams/{stream_id}/chunks", json={"samples": [[1.0] * 32]})
    resp = client.post(f"/streams/{stream_id}/reset")
    assert resp.status_code == 200
    resp = client.get(f"/streams/{stream_id}")
    assert resp.status_code == 200
    assert resp.json()["n_sections"] == 2
    resp = client.delete(f"/streams/{stream_id}")
    assert resp.status_code == 204
    assert client.get(f"/streams/{stream_id}").status_code == 404
