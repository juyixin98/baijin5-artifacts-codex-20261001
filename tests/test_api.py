"""End-to-end API tests via FastAPI TestClient (no network, no accounts)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from wsola_backend import fixtures
from wsola_backend.main import app

client = TestClient(app)


def post(payload: dict):
    return client.post("/v1/time-stretch", json=payload)


def tone_payload(rate: float, request_id: str | None = "req-test-1") -> dict:
    payload = {
        "sample_rate": 16_000,
        "rate": rate,
        "samples": fixtures.tone(440.0, 0.25).tolist(),
    }
    if request_id is not None:
        payload["request_id"] = request_id
    return payload


def diagnostic_codes(body: dict) -> list[str]:
    return [d["code"] for d in body["diagnostics"]]


def test_healthz():
    assert client.get("/healthz").json() == {"status": "ok"}


def test_happy_path_returns_offsets_and_exact_length():
    response = post(tone_payload(rate=1.5))
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "req-test-1"
    n_in = 4000
    assert body["stats"]["input_length"] == n_in
    assert body["stats"]["output_length"] == round(n_in / 1.5)
    assert len(body["samples"]) == round(n_in / 1.5)
    assert body["segments"], "per-segment offsets must be present"
    assert body["segments"][0]["delta"] == 0
    assert body["segments"][0]["correlation"] is None
    for seg in body["segments"]:
        assert -256 <= seg["delta"] <= 256
        assert seg["analysis_position"] == seg["nominal_position"] + seg["delta"]
    assert "REQUEST_ACCEPTED" in diagnostic_codes(body)


def test_request_id_generated_when_omitted():
    body = post(tone_payload(rate=1.0, request_id=None)).json()
    assert body["request_id"] and len(body["request_id"]) == 32


def test_rate_beyond_hard_limit_rejected_422():
    response = post(tone_payload(rate=10.0))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"]["code"] == "RATE_OUT_OF_RANGE"
    assert detail["request_id"] == "req-test-1"
    assert any(d["code"] == "REQUEST_RECEIVED" for d in detail["diagnostics"])


def test_rate_outside_quality_range_accepted_with_warning():
    response = post(tone_payload(rate=3.0))
    assert response.status_code == 200
    assert "RATE_OUTSIDE_QUALITY_RANGE" in diagnostic_codes(response.json())


def test_too_short_input_rejected_422():
    payload = {"sample_rate": 16_000, "rate": 1.0, "samples": [0.1] * 100}
    response = post(payload)
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "INPUT_TOO_SHORT"


def test_silence_reports_undecidable_match_rule():
    payload = {
        "sample_rate": 16_000,
        "rate": 1.2,
        "samples": fixtures.silence(4096).tolist(),
    }
    body = post(payload).json()
    assert "DEGENERATE_MATCH" in diagnostic_codes(body)
    assert all(seg["delta"] == 0 for seg in body["segments"])


def test_diagnostics_never_carry_raw_samples():
    body = post(tone_payload(rate=1.5)).json()
    for record in body["diagnostics"]:
        assert "samples" not in record["context"]
        assert "audio" not in record["context"]
