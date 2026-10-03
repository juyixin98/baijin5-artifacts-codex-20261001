"""Integration tests for the FastAPI boundary.

These assert concrete numerical outcomes and failure categories through
the HTTP layer — not merely that endpoints are callable.
"""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from app.main import app
from app.verification import TOEPLITZ_CROSSCHECK_TOL
from app.lpc.reference import solve_toeplitz_reference
from app.lpc.autocorr import apply_window, autocorrelation

client = TestClient(app)


def _post(url: str, payload: dict, request_id: str = "itest-req-1"):
    return client.post(url, json=payload, headers={"X-Request-ID": request_id})


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_analyze_ar_signal_recovers_generating_coefficients(ar_signal, expected):
    """LPC(order=4) of an AR(4) process must land near the known
    generating coefficients (statistical tolerance from expected.json)."""
    cfg = {"frame_size": 512, "order": 4, "window": "hann"}
    resp = _post("/v1/lpc/analyze", {"samples": ar_signal["samples"].tolist(), "config": cfg})
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "itest-req-1"
    assert body["processing"]["version"]
    assert body["processing"]["n_frames"] == len(body["frames"])

    a_true = np.array(expected["ar"]["a_true"])
    tol = expected["ar"]["recovery_tolerance"]
    estimates = np.array([f["lpc"] for f in body["frames"]])
    mean_est = estimates.mean(axis=0)
    np.testing.assert_allclose(mean_est, a_true, rtol=0, atol=tol)
    for frame in body["frames"]:
        assert frame["stability"]["stable"], frame


def test_analyze_matches_independent_toeplitz_reference(noise):
    """API coefficients must match a Toeplitz solve computed here in the
    test — an independent path that does not reuse the core recursion.
    White noise keeps the normal equations well-conditioned so the two
    solvers must agree tightly."""
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    samples = noise.tolist()
    resp = _post("/v1/lpc/analyze", {"samples": samples, "config": cfg})
    assert resp.status_code == 200
    body = resp.json()
    assert body["uncertain"] == []
    frames = body["frames"]
    for i, frame in enumerate(frames):
        raw = np.array(samples[i * 256 : (i + 1) * 256])
        r = autocorrelation(apply_window(raw, "hann"), 10)
        ref = solve_toeplitz_reference(r, 10)
        np.testing.assert_allclose(
            frame["lpc"], ref, rtol=0, atol=TOEPLITZ_CROSSCHECK_TOL
        )


def test_roundtrip_ar_signal_is_lossless(ar_signal):
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    resp = _post(
        "/v1/lpc/roundtrip",
        {"samples": ar_signal["samples"][:2048].tolist(), "config": cfg},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "lossless"
    assert body["lossless_confirmed"] is True
    assert body["metrics"]["relative_error"] < 1e-9
    assert body["metrics"]["max_abs_error"] < 1e-8


def test_silence_frames_are_zero_energy(silence):
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    resp = _post("/v1/lpc/analyze", {"samples": silence.tolist(), "config": cfg})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["frames"]) == 2
    for frame in body["frames"]:
        assert frame["zero_energy"] is True
        assert frame["lpc"] == [1.0] + [0.0] * 10
        assert frame["gain"] == 0.0
        assert all(v == 0.0 for v in frame["residual"])
        assert "zero_energy_frame" in frame["stability"]["diagnostics"]
    assert any("zero_energy_frame" in d for d in body["diagnostics"])


def test_order_at_or_above_frame_size_is_rejected():
    cfg = {"frame_size": 64, "order": 64, "window": "hann"}
    resp = _post("/v1/lpc/analyze", {"samples": [0.1] * 128, "config": cfg})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["category"] == "invalid_request"
    assert "order" in body["error"]["message"]
    assert body["request_id"] == "itest-req-1"


def test_high_order_on_sinusoid_reports_uncertain_conclusion(sine_signal):
    """Order 40 on a rank-2-ish sinusoid makes the normal equations
    ill-conditioned: the API must list an uncertain conclusion from the
    independent Toeplitz cross-check, not report silent success."""
    cfg = {"frame_size": 256, "order": 40, "window": "hann"}
    resp = _post(
        "/v1/lpc/analyze",
        {"samples": sine_signal["samples"].tolist(), "config": cfg},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["uncertain"], "expected uncertain conclusions for ill-conditioned solve"
    assert any("deviation" in u for u in body["uncertain"])
    # every frame still carries a finite, interpretable result
    for frame in body["frames"]:
        assert all(np.isfinite(v) for v in frame["lpc"])
        assert frame["stability"]["max_abs_reflection"] >= 0.0


def test_analyze_reconstruct_roundtrip_via_endpoints(noise):
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    samples = noise.tolist()
    ra = _post("/v1/lpc/analyze", {"samples": samples, "config": cfg})
    assert ra.status_code == 200
    frames = [
        {"lpc": f["lpc"], "residual": f["residual"], "zero_energy": f["zero_energy"]}
        for f in ra.json()["frames"]
    ]
    rr = _post("/v1/lpc/reconstruct", {"frames": frames, "config": cfg})
    assert rr.status_code == 200
    reconstructed = np.array(rr.json()["samples"][: len(samples)])
    np.testing.assert_allclose(reconstructed, np.array(samples), rtol=0, atol=1e-9)


def test_reconstruct_rejects_order_mismatch(noise):
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    bad_frame = {"lpc": [1.0, 0.1, 0.1], "residual": [0.0] * 256, "zero_energy": False}
    resp = _post("/v1/lpc/reconstruct", {"frames": [bad_frame], "config": cfg})
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "invalid_config"


def test_non_finite_samples_rejected():
    # json= would refuse to serialize inf client-side; send raw JSON.
    resp = client.post(
        "/v1/lpc/analyze",
        content='{"samples": [0.0, Infinity, 1.0]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "invalid_request"


def test_stream_session_matches_one_shot(noise):
    """Streaming two frames through a session must equal one-shot
    analysis of the concatenation (state correspondence end-to-end)."""
    cfg = {"frame_size": 256, "order": 10, "window": "hann"}
    session = "itest-session-1"
    client.delete(f"/v1/lpc/stream/{session}")
    streamed_residuals = []
    streamed_recon = []
    for i in range(2):
        chunk = noise[i * 256 : (i + 1) * 256].tolist()
        resp = _post(
            f"/v1/lpc/stream/{session}/frame", {"samples": chunk, "config": cfg}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["frame_index"] == i
        streamed_residuals.extend(body["residual"])
        streamed_recon.extend(body["reconstructed"])

    one_shot = _post(
        "/v1/lpc/analyze", {"samples": noise[:512].tolist(), "config": cfg}
    ).json()
    one_shot_residuals = [
        v for f in one_shot["frames"] for v in f["residual"]
    ]
    np.testing.assert_allclose(
        streamed_residuals, one_shot_residuals, rtol=0, atol=1e-12
    )
    np.testing.assert_allclose(
        streamed_recon, noise[:512], rtol=0, atol=1e-10
    )


def test_request_id_is_generated_when_header_missing(noise):
    resp = client.post(
        "/v1/lpc/analyze",
        json={"samples": noise[:256].tolist(), "config": {"frame_size": 256, "order": 10}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"]
    assert resp.headers["X-Request-ID"] == body["request_id"]
