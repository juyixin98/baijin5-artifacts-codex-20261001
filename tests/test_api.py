"""HTTP API tests: success path, per-category rejections, request id echo."""
import base64

import numpy as np
import pytest
from fastapi.testclient import TestClient

from fixtures.synth import get_fixture
from wsola_backend.api import app

client = TestClient(app)


def encode(samples: np.ndarray) -> str:
    return base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode("ascii")


def decode(payload: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(payload), dtype="<f4")


def post(samples, ts=1.5, sr=16_000, headers=None, label=None):
    body = {"sample_rate": sr, "time_scale": ts, "samples_b64": encode(samples)}
    if label is not None:
        body["input_label"] = label
    return client.post("/v1/stretch", json=body, headers=headers or {})


def test_success_returns_offsets_and_exact_length():
    sr, x = get_fixture("tone_440hz")
    resp = post(x, ts=1.5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] in ("accepted", "accepted_with_notes")
    assert body["target_length"] == round(x.shape[0] * 1.5)
    assert decode(body["samples_b64"]).shape[0] == body["target_length"]
    assert len(body["offsets"]) == body["n_frames"]
    assert len(body["match_positions"]) == body["n_frames"]


def test_request_id_echoed_and_generated():
    sr, x = get_fixture("tone_440hz")
    resp = post(x[:4096], ts=1.0, headers={"X-Request-ID": "test-req-1"})
    assert resp.json()["request_id"] == "test-req-1"
    assert resp.headers["x-request-id"] == "test-req-1"
    resp2 = post(x[:4096], ts=1.0)
    assert resp2.json()["request_id"]


@pytest.mark.parametrize("ts", [0.49, 2.01])
def test_unsupported_time_scale_422(ts):
    _, x = get_fixture("tone_440hz")
    resp = post(x[:4096], ts=ts)
    assert resp.status_code == 422
    assert resp.json()["failure_category"] == "unsupported_time_scale"
    assert resp.json()["decision"] == "rejected"


def test_non_finite_samples_422():
    x = np.zeros(4096)
    x[7] = np.nan
    resp = post(x)
    assert resp.status_code == 422
    assert resp.json()["failure_category"] == "non_finite_samples"


def test_empty_input_422():
    resp = post(np.zeros(0))
    assert resp.status_code == 422
    assert resp.json()["failure_category"] == "empty_input"


def test_too_short_input_422():
    resp = post(np.zeros(100))
    assert resp.status_code == 422
    assert resp.json()["failure_category"] == "input_too_short"


def test_malformed_payload_422():
    resp = client.post("/v1/stretch", json={
        "sample_rate": 16_000, "time_scale": 1.0, "samples_b64": "!!!not-base64!!!",
    })
    assert resp.status_code == 422
    assert resp.json()["failure_category"] == "malformed_payload"


def test_silence_is_undecidable():
    _, x = get_fixture("silence")
    resp = post(x, ts=1.5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "undecidable"
    assert all(o == 0 for o in body["offsets"])
    assert np.all(decode(body["samples_b64"]) == 0.0)


def test_label_not_logged_in_plaintext(caplog):
    sr, x = get_fixture("tone_440hz")
    secret_label = "customer-voice-12345"
    with caplog.at_level("INFO", logger="wsola_backend"):
        resp = post(x[:4096], ts=1.0, label=secret_label)
    assert resp.status_code == 200
    assert secret_label not in caplog.text
    assert "sha256:" in caplog.text
