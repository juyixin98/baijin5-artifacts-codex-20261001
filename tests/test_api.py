"""API contract tests via FastAPI TestClient."""

import numpy as np
from fastapi.testclient import TestClient

from mfcc_backend.api import app

client = TestClient(app)


def test_healthz_reports_versions(tlog):
    resp = client.get("/healthz")
    body = resp.json()
    tlog.step("healthz", basis="200 + versions block",
              status=resp.status_code, versions=body.get("versions"))
    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert {"service", "python", "numpy", "scipy"} <= set(body["versions"])


def test_batch_mfcc_happy_path(sine_440, tlog):
    x, sr, input_id = sine_440
    resp = client.post("/v1/mfcc", json={"samples": x.tolist(), "sample_rate": sr})
    body = resp.json()
    tlog.step("batch_200", input_id=input_id,
              basis="98 frames of 13 coeffs; run_id present; config echoed",
              run_id=body.get("run_id"), n_frames=body.get("n_frames"))
    assert resp.status_code == 200
    assert body["run_id"]
    assert body["n_frames"] == 98
    assert body["mfcc"]["n_frames"] == 98
    assert body["mfcc"]["n_coeffs"] == 13
    assert body["delta"]["n_frames"] == 98
    assert body["delta_delta"]["n_frames"] == 98
    assert body["versions"]["service"]


def test_batch_short_input_is_zero_frames_not_error(short_input, tlog):
    x, sr, input_id = short_input
    resp = client.post("/v1/mfcc", json={"samples": x.tolist(), "sample_rate": sr})
    body = resp.json()
    tlog.step("batch_short", input_id=input_id,
              basis="200 with n_frames=0 and a warning, not an error",
              n_frames=body.get("n_frames"), warnings=body.get("warnings"))
    assert resp.status_code == 200
    assert body["n_frames"] == 0
    assert body["mfcc"]["values"] == []
    assert body["warnings"], "short input must be flagged in warnings"


def test_batch_nan_rejected_with_category(tlog):
    # httpx refuses to serialize NaN via json=, so send the raw JSON body
    # (Python's json parser on the server side accepts the NaN literal).
    samples = "[0.0, NaN, 0.1, " + ", ".join(["0.05"] * 597) + "]"
    resp = client.post(
        "/v1/mfcc",
        content=f'{{"samples": {samples}, "sample_rate": 16000}}',
        headers={"content-type": "application/json"},
    )
    body = resp.json()
    tlog.step("batch_nan", basis="400 + category=input_contract + run_id",
              status=resp.status_code, body=body)
    assert resp.status_code == 400
    assert body["error"]["category"] == "input_contract"
    assert body["error"]["run_id"]


def test_batch_empty_samples_rejected(tlog):
    resp = client.post("/v1/mfcc", json={"samples": [], "sample_rate": 16000})
    tlog.step("batch_empty", basis="schema min_length=1 -> 422",
              status=resp.status_code)
    assert resp.status_code == 422


def test_batch_invalid_config_rejected(tlog):
    resp = client.post("/v1/mfcc", json={
        "samples": [0.0] * 1000, "sample_rate": 16000,
        "config": {"n_fft": 64},  # < frame_length
    })
    body = resp.json()
    tlog.step("batch_bad_config", basis="400 + category=config",
              status=resp.status_code, body=body)
    assert resp.status_code == 400
    assert body["error"]["category"] == "config"


def test_batch_empty_filterbank_rejected(tlog):
    resp = client.post("/v1/mfcc", json={
        "samples": [0.01] * 1000, "sample_rate": 8000,
        "config": {"n_fft": 64, "window_ms": 8.0, "hop_ms": 4.0, "n_mels": 40},
    })
    body = resp.json()
    tlog.step("batch_empty_filterbank",
              basis="400 + category=filterbank_empty_support",
              status=resp.status_code, category=body.get("error", {}).get("category"))
    assert resp.status_code == 400
    assert body["error"]["category"] == "filterbank_empty_support"
    assert body["error"]["details"]["empty_filters"]


def test_streaming_session_flow_matches_batch(sine_440, tlog):
    x, sr, input_id = sine_440
    create = client.post("/v1/stream", json={"sample_rate": sr})
    assert create.status_code == 200
    sid = create.json()["session_id"]

    frames, deltas, deltas2 = [], [], []
    for pos in range(0, len(x), 777):
        chunk = x[pos : pos + 777].tolist()
        resp = client.post(f"/v1/stream/{sid}/chunks", json={"samples": chunk})
        assert resp.status_code == 200
        body = resp.json()
        frames += body["mfcc"]["values"]
        deltas += body["delta"]["values"]
        deltas2 += body["delta_delta"]["values"]
    fin = client.post(f"/v1/stream/{sid}/finalize")
    assert fin.status_code == 200
    body = fin.json()
    frames += body["mfcc"]["values"]
    deltas += body["delta"]["values"]
    deltas2 += body["delta_delta"]["values"]

    batch = client.post("/v1/mfcc",
                        json={"samples": x.tolist(), "sample_rate": sr}).json()
    tlog.step("stream_api_vs_batch", input_id=input_id,
              basis="API stream (777-sample chunks) == API batch, atol=1e-9",
              stream_frames=len(frames), batch_frames=batch["n_frames"])
    assert len(frames) == batch["n_frames"] == 98
    np.testing.assert_allclose(frames, batch["mfcc"]["values"], atol=1e-9, rtol=0)
    np.testing.assert_allclose(deltas, batch["delta"]["values"], atol=1e-9, rtol=0)
    np.testing.assert_allclose(deltas2, batch["delta_delta"]["values"], atol=1e-9, rtol=0)


def test_streaming_unknown_session_404(tlog):
    resp = client.post("/v1/stream/deadbeef/chunks", json={"samples": [0.0] * 10})
    tlog.step("stream_404", basis="unknown session -> 404",
              status=resp.status_code)
    assert resp.status_code == 404


def test_streaming_chunk_after_finalize_400(tlog):
    create = client.post("/v1/stream", json={"sample_rate": 16000})
    sid = create.json()["session_id"]
    client.post(f"/v1/stream/{sid}/chunks", json={"samples": [0.0] * 500})
    client.post(f"/v1/stream/{sid}/finalize")
    resp = client.post(f"/v1/stream/{sid}/chunks", json={"samples": [0.0] * 500})
    body = resp.json()
    tlog.step("stream_after_finalize", basis="400 + category=stream_state",
              status=resp.status_code, category=body.get("error", {}).get("category"))
    assert resp.status_code == 400
    assert body["error"]["category"] == "stream_state"
