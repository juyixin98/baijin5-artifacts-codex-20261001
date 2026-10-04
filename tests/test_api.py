"""API tests: contracts, request identity, and typed failure categories."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

PARAMS = {"n_fft": 128, "win_length": 128, "hop_length": 32, "window": "hann"}


def make_signal(n=500, seed=1):
    return np.random.default_rng(seed).standard_normal(n).tolist()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_stft_response_contract_and_request_id():
    samples = make_signal()
    r = client.post("/v1/stft", json={"samples": samples, "params": PARAMS})
    assert r.status_code == 200
    body = r.json()
    assert body["request_id"]
    assert r.headers["X-Request-ID"] == body["request_id"]
    assert body["n_samples"] == len(samples)
    assert body["n_bins"] == 65
    assert body["n_frames"] == len(body["spectrogram"])
    # Frame centres are k * hop and there is one per frame.
    assert body["frame_centers"] == [
        k * PARAMS["hop_length"] for k in range(body["n_frames"])
    ]
    frame0 = body["spectrogram"][0]
    assert len(frame0["real"]) == len(frame0["imag"]) == 65


def test_api_roundtrip_preserves_signal_and_length():
    samples = make_signal(n=517)
    r1 = client.post("/v1/stft", json={"samples": samples, "params": PARAMS})
    assert r1.status_code == 200
    spec = r1.json()["spectrogram"]

    r2 = client.post(
        "/v1/istft",
        json={"spectrogram": spec, "params": PARAMS, "n_samples": len(samples)},
    )
    assert r2.status_code == 200
    body = r2.json()
    assert body["n_samples"] == len(samples)
    assert body["min_ola_denominator"] > 0
    np.testing.assert_allclose(body["samples"], samples, atol=1e-9)


def test_not_reconstructible_is_typed_failure():
    bad = dict(PARAMS, hop_length=256)  # hop > win_length
    r = client.post(
        "/v1/stft", json={"samples": make_signal(100), "params": bad}
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"]["code"] == "NOT_RECONSTRUCTIBLE"
    assert body["request_id"]
    assert "hop" in body["error"]["message"]


def test_shape_mismatch_is_typed_failure():
    spec = [{"real": [0.0] * 10, "imag": [0.0] * 10} for _ in range(3)]
    r = client.post(
        "/v1/istft",
        json={"spectrogram": spec, "params": PARAMS, "n_samples": 100},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SHAPE_MISMATCH"


def test_ragged_frame_is_typed_failure():
    spec = [{"real": [0.0] * 65, "imag": [0.0] * 64}]
    r = client.post(
        "/v1/istft",
        json={"spectrogram": spec, "params": PARAMS, "n_samples": 100},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SHAPE_MISMATCH"


def test_schema_validation_failure():
    r = client.post(
        "/v1/stft",
        json={"samples": make_signal(10), "params": dict(PARAMS, n_fft=-4)},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "SCHEMA_VALIDATION"


def test_empty_signal_failure():
    r = client.post("/v1/stft", json={"samples": [], "params": PARAMS})
    assert r.status_code == 422  # contract requires min_length=1


def test_expand_endpoint_restores_full_spectrum():
    samples = make_signal(200)
    r1 = client.post("/v1/stft", json={"samples": samples, "params": PARAMS})
    spec = r1.json()["spectrogram"]
    r2 = client.post("/v1/expand", json={"spectrogram": spec, "n_fft": 128})
    assert r2.status_code == 200
    body = r2.json()
    assert body["n_bins_full"] == 128
    row = body["spectrogram"][0]
    full = np.asarray(row["real"]) + 1j * np.asarray(row["imag"])
    # DC and Nyquist kept once; mirrored bins conjugate-symmetric.
    assert full[0] == complex(spec[0]["real"][0], spec[0]["imag"][0])
    assert full[64] == complex(spec[0]["real"][64], spec[0]["imag"][64])
    for k in range(1, 64):
        assert full[k] == pytest.approx(np.conj(full[128 - k]))


def test_expand_rejects_wrong_n_fft():
    spec = [{"real": [0.0] * 65, "imag": [0.0] * 65}]
    r = client.post("/v1/expand", json={"spectrogram": spec, "n_fft": 256})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "SHAPE_MISMATCH"
