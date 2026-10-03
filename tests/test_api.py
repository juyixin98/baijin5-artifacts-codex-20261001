"""API contract tests: success shapes, structured error bodies with the
right status codes, and REST streaming equivalence with the batch route.
Errors must never surface as HTTP 200."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from mfcc_backend import fixtures
from mfcc_backend.service import app, versions

client = TestClient(app)


def test_health_reports_versions():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    for key in ("python", "numpy", "scipy", "fastapi", "mfcc_backend"):
        assert key in body["versions"]
    assert body["versions"] == versions()


def test_features_happy_path(run_log):
    x = fixtures.make_sine(440.0, duration_s=0.25)
    r = client.post("/v1/features", json={"sample_rate": 16000, "samples": x.tolist()})
    assert r.status_code == 200
    body = r.json()
    n_frames = 1 + (len(x) - 400) // 160
    assert body["meta"]["n_frames"] == n_frames
    assert body["meta"]["feature_shape"] == [n_frames, 13]
    assert body["meta"]["input_sha1"]
    assert body["request_id"]
    assert len(body["features"]["mfcc"]) == n_frames
    assert len(body["features"]["delta"][0]) == 13
    assert len(body["features"]["delta2"]) == n_frames
    run_log("api_features", n_frames=n_frames, request_id=body["request_id"],
            verdict="pass", rationale="shape = (1+(n-400)//160, 13) for mfcc/delta/delta2")


def test_features_include_intermediates():
    x = fixtures.make_sine(440.0, duration_s=0.1)
    r = client.post("/v1/features", json={
        "sample_rate": 16000, "samples": x.tolist(), "include_intermediates": True})
    assert r.status_code == 200
    inter = r.json()["intermediates"]
    assert set(inter) == {"frames", "power_spectrum", "mel_energies", "log_mel"}
    assert len(inter["frames"][0]) == 400
    assert len(inter["power_spectrum"][0]) == 201
    assert len(inter["mel_energies"][0]) == 26


@pytest.mark.parametrize(
    "payload,status,code",
    [
        ({"sample_rate": 16000, "samples": []}, 422, None),  # pydantic min_length
        ({"sample_rate": 16000, "samples": [0.1] * 100}, 422, "INSUFFICIENT_SIGNAL"),
        ({"sample_rate": 16000, "samples": fixtures.make_sine(duration_s=0.1).tolist(),
          "config": {"n_mfcc": 30}}, 422, "INVALID_CONFIG"),
        ({"sample_rate": 8000, "samples": fixtures.make_sine(duration_s=0.1).tolist(),
          "config": {"frame_length_ms": 1.0, "hop_length_ms": 0.5}}, 422, "EMPTY_FILTER_SUPPORT"),
        ({"sample_rate": 16000, "samples": fixtures.make_sine(duration_s=0.1).tolist(),
          "config": {"bogus_field": 1}}, 422, None),  # extra=forbid
    ],
)
def test_error_semantics(payload, status, code, run_log):
    r = client.post("/v1/features", json=payload)
    assert r.status_code == status, r.text
    assert r.status_code != 200
    if code is not None:
        body = r.json()
        assert body["error"]["code"] == code
        assert body["error"]["message"]
        run_log("api_error_semantics", expected_code=code, http_status=r.status_code,
                verdict="pass", rationale="failure maps to structured non-200 error")


def test_nan_samples_rejected_with_invalid_audio(run_log):
    # httpx's strict JSON encoder refuses NaN, so send the raw body; the
    # server-side parser accepts the NaN literal and the pipeline must
    # reject it as INVALID_AUDIO (400), never compute on it.
    samples = "0.0, NaN, 1.0," * 200
    r = client.post(
        "/v1/features",
        content=f'{{"sample_rate": 16000, "samples": [{samples}0.5]}}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_AUDIO"
    run_log("api_nan_rejected", http_status=r.status_code, verdict="pass",
            rationale="non-finite samples map to 400 INVALID_AUDIO")


def test_unknown_session_is_404():
    r = client.post("/v1/stream/sessions/does-not-exist/chunks",
                    json={"samples": [0.0] * 160})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_streaming_api_matches_batch_api(run_log):
    x = fixtures.make_white_noise(duration_s=0.3, seed=5)
    batch = client.post("/v1/features",
                        json={"sample_rate": 16000, "samples": x.tolist()}).json()

    start = client.post("/v1/stream/sessions", json={"sample_rate": 16000})
    assert start.status_code == 201
    sid = start.json()["session_id"]

    mfcc, delta, delta2 = [], [], []
    pos = 0
    for size in (500, 160, 1200, 777, 4000):
        chunk = x[pos : pos + size]
        pos += size
        if not len(chunk):
            continue
        r = client.post(f"/v1/stream/sessions/{sid}/chunks",
                        json={"samples": chunk.tolist()})
        assert r.status_code == 200
        em = r.json()["emitted"]
        mfcc += em["mfcc"]
        delta += em["delta"]
        delta2 += em["delta2"]
    r = client.post(f"/v1/stream/sessions/{sid}/finish")
    assert r.status_code == 200
    em = r.json()["emitted"]
    mfcc += em["mfcc"]
    delta += em["delta"]
    delta2 += em["delta2"]

    np.testing.assert_allclose(np.array(mfcc), np.array(batch["features"]["mfcc"]),
                               rtol=0, atol=1e-12)
    np.testing.assert_allclose(np.array(delta), np.array(batch["features"]["delta"]),
                               rtol=0, atol=1e-12)
    np.testing.assert_allclose(np.array(delta2), np.array(batch["features"]["delta2"]),
                               rtol=0, atol=1e-12)
    run_log("api_stream_equals_batch", session_id=sid, frames=len(mfcc),
            verdict="pass", rationale="REST stream concatenation equals /v1/features")


def test_stream_session_lifecycle_errors():
    sid = client.post("/v1/stream/sessions", json={}).json()["session_id"]
    x = fixtures.make_sine(duration_s=0.1).tolist()
    assert client.post(f"/v1/stream/sessions/{sid}/chunks", json={"samples": x}).status_code == 200
    assert client.post(f"/v1/stream/sessions/{sid}/finish").status_code == 200
    # chunk after finish -> 409, never 200
    r = client.post(f"/v1/stream/sessions/{sid}/chunks", json={"samples": [0.0] * 160})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "SESSION_STATE_ERROR"


def test_stream_too_short_finish_is_error_not_empty_success():
    sid = client.post("/v1/stream/sessions", json={}).json()["session_id"]
    client.post(f"/v1/stream/sessions/{sid}/chunks", json={"samples": [0.01] * 50})
    r = client.post(f"/v1/stream/sessions/{sid}/finish")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSUFFICIENT_SIGNAL"
