"""End-to-end API tests via FastAPI TestClient.

These assert concrete numerical results and failure categories — not just
that endpoints are callable. Reference answers come from the committed
fixtures (true AR coefficients, sine frequency) and from independent
solvers, never from the core under test alone.
"""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.version import __version__, PIPELINE_VERSION
from tests.conftest import load_fixture


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app())


def _post(client: TestClient, url: str, payload: dict, **kwargs):
    resp = client.post(url, json=payload, **kwargs)
    assert resp.status_code == 200, resp.text
    return resp


# ------------------------------------------------------------------ meta


def test_health_and_version(client: TestClient):
    assert client.get("/health").json() == {"status": "ok", "version": __version__}
    body = client.get("/v1/version").json()
    assert body["version"] == __version__
    assert body["pipeline_version"] == PIPELINE_VERSION
    assert body["defaults"]["order"] == 10


def test_request_identity_is_correlated(client: TestClient):
    fixture = load_fixture("sine.json")
    resp = _post(
        client,
        "/v1/lpc/analyze",
        {"samples": fixture["samples"][:160], "order": 2},
        headers={"X-Request-ID": "test-req-123"},
    )
    assert resp.headers["X-Request-ID"] == "test-req-123"
    body = resp.json()
    assert body["meta"]["request_id"] == "test-req-123"
    assert body["meta"]["version"] == __version__
    assert body["meta"]["pipeline_version"] == PIPELINE_VERSION
    assert body["meta"]["stages"] == [
        "window",
        "autocorrelation",
        "levinson-durbin",
        "pole-stability",
        "analysis-filter",
    ]


# ------------------------------------------------------------------ analyze


def test_analyze_ar_fixture_recovers_true_coefficients(client: TestClient):
    fixture = load_fixture("ar_process.json")
    body = _post(
        client,
        "/v1/lpc/analyze",
        {
            "samples": fixture["samples"],
            "order": 4,
            "window": "rect",
            "verify_toeplitz": True,
        },
    ).json()
    analysis = body["analysis"]
    estimated = np.asarray(analysis["coefficients"])
    true = np.asarray(fixture["true_coefficients"])
    np.testing.assert_allclose(estimated, true, atol=0.1)
    assert analysis["stable"] is True
    assert analysis["errors"] == []
    assert body["toeplitz_crosscheck"]["agrees"] is True
    assert body["toeplitz_crosscheck"]["max_abs_deviation"] < 1e-6


def test_analyze_sine_finds_conjugate_pole_pair(client: TestClient):
    fixture = load_fixture("sine.json")
    body = _post(
        client, "/v1/lpc/analyze", {"samples": fixture["samples"], "order": 2}
    ).json()
    analysis = body["analysis"]
    omega = 2 * np.pi * fixture["frequency_hz"] / fixture["sample_rate"]
    assert analysis["coefficients"][1] == pytest.approx(-2 * np.cos(omega), abs=0.01)
    assert analysis["coefficients"][2] == pytest.approx(1.0, abs=0.01)
    assert analysis["max_pole_magnitude"] > 0.99
    assert analysis["stable"] is True


def test_analyze_silence_has_defined_zero_energy_behaviour(client: TestClient):
    fixture = load_fixture("silence.json")
    body = _post(client, "/v1/lpc/analyze", {"samples": fixture["samples"]}).json()
    analysis = body["analysis"]
    assert analysis["coefficients"] == [1.0] + [0.0] * 10
    assert analysis["gain"] == 0.0
    assert all(s == 0.0 for s in analysis["residual"])
    codes = [d["code"] for d in analysis["diagnostics"]]
    assert codes == ["ZERO_ENERGY_FRAME"]
    assert analysis["errors"] == []


def test_analyze_order_too_high_fixture_reports_uncertainty(client: TestClient):
    fixture = load_fixture("rank_deficient.json")
    body = _post(
        client,
        "/v1/lpc/analyze",
        {"samples": fixture["samples"], "order": 20, "verify_toeplitz": True},
    ).json()
    assert body["toeplitz_crosscheck"]["agrees"] is False
    analysis = body["analysis"]
    codes = [d["code"] for d in analysis["diagnostics"]]
    assert "TOEPLITZ_CROSSCHECK_MISMATCH" in codes
    # uncertain conclusions are listed separately from hard failures
    assert analysis["warnings"] != []


# ------------------------------------------------------------------ roundtrip


def test_roundtrip_verifies_against_original_not_residual(client: TestClient):
    fixture = load_fixture("ar_process.json")
    body = _post(
        client,
        "/v1/lpc/roundtrip",
        {"samples": fixture["samples"][:320], "order": 10},
    ).json()
    metrics = body["metrics"]
    assert metrics["verified"] is True
    assert metrics["relative_error"] <= metrics["tolerance"]
    assert metrics["residual_energy_ratio"] < 0.2
    assert "residual" in metrics["note"]  # verification basis is documented
    reconstructed = np.asarray(body["reconstructed"])
    original = np.asarray(fixture["samples"][:320])
    np.testing.assert_allclose(reconstructed, original, rtol=1e-6, atol=1e-9)


def test_analyze_then_synthesize_with_corresponding_state(client: TestClient):
    fixture = load_fixture("ar_process.json")
    samples = fixture["samples"][:320]
    analysis = _post(
        client, "/v1/lpc/analyze", {"samples": samples, "order": 10}
    ).json()["analysis"]
    # one-shot analysis started from zero state, so synthesis must too
    synth = _post(
        client,
        "/v1/lpc/synthesize",
        {
            "coefficients": analysis["coefficients"],
            "residual": analysis["residual"],
        },
    ).json()
    np.testing.assert_allclose(synth["samples"], samples, rtol=1e-6, atol=1e-9)
    assert len(synth["final_state"]) == 10


# ------------------------------------------------------------------ streams


def test_stream_roundtrip_reconstructs_concatenated_signal(client: TestClient):
    fixture = load_fixture("ar_process.json")
    samples = fixture["samples"]
    created = client.post("/v1/lpc/streams", json={"order": 10}).json()
    stream_id = created["stream_id"]

    frame_len = 320
    reconstructed = []
    for i in range(0, len(samples), frame_len):
        body = _post(
            client,
            f"/v1/lpc/streams/{stream_id}/frames",
            {"samples": samples[i : i + frame_len], "mode": "roundtrip"},
        ).json()
        reconstructed.extend(body["reconstructed"])
        assert body["metrics"]["verified"] is True

    rel_err = np.linalg.norm(np.asarray(reconstructed) - np.asarray(samples))
    rel_err /= np.linalg.norm(np.asarray(samples))
    assert rel_err <= 1e-9

    state = client.get(f"/v1/lpc/streams/{stream_id}").json()
    assert state["frames_processed"] == len(samples) // frame_len
    assert len(state["analysis_state"]) == 10

    assert client.delete(f"/v1/lpc/streams/{stream_id}").status_code == 204
    missing = client.get(f"/v1/lpc/streams/{stream_id}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "STREAM_NOT_FOUND"


# ------------------------------------------------------------------ failures


def test_order_not_smaller_than_frame_is_a_domain_error(client: TestClient):
    resp = client.post("/v1/lpc/analyze", json={"samples": [1.0] * 8, "order": 8})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "INVALID_FRAME_PARAMETERS"
    assert body["request_id"] != "-"


def test_nan_samples_are_rejected(client: TestClient):
    # httpx refuses to serialize NaN, so post the raw JSON body; the
    # server's JSON parser accepts the NaN literal and the contract
    # validator must reject it.
    resp = client.post(
        "/v1/lpc/analyze",
        content='{"samples": [1.0, NaN, 2.0]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_window_is_rejected(client: TestClient):
    resp = client.post(
        "/v1/lpc/analyze", json={"samples": [1.0] * 32, "window": "blackman"}
    )
    assert resp.status_code == 422


def test_synthesize_requires_matching_state_length(client: TestClient):
    resp = client.post(
        "/v1/lpc/synthesize",
        json={
            "coefficients": [1.0, -0.5],
            "residual": [0.1, 0.2, 0.3],
            "initial_state": [0.0, 0.0],
        },
    )
    assert resp.status_code == 422


def test_synthesize_rejects_non_unit_leading_coefficient(client: TestClient):
    resp = client.post(
        "/v1/lpc/synthesize",
        json={"coefficients": [2.0, -0.5], "residual": [0.1, 0.2]},
    )
    assert resp.status_code == 422
