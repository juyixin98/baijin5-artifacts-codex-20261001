"""End-to-end API tests via FastAPI's in-process ASGI transport.

These assert concrete numerical results and explicit failure categories —
not merely that endpoints respond.
"""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from stft_backend.api import create_app
from stft_backend.config import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    settings = Settings(
        default_nperseg=8,
        default_hop=4,
        default_window="hann",
        log_level="WARNING",
        max_signal_samples=100_000,
        max_frames_per_session=1000,
    )
    return TestClient(create_app(settings))


def test_health_and_info(client: TestClient) -> None:
    health = client.get("/health").json()
    assert health == {"status": "ok", "service": "stft-backend",
                      "version": "1.0.0"}
    info = client.get("/v1/info").json()
    assert info["service"] == "stft-backend"
    assert info["version"] == "1.0.0"
    assert info["defaults"]["nperseg"] == 8
    assert info["processing_location"]["host"]


def test_request_id_is_echoed_and_generated(client: TestClient) -> None:
    response = client.post(
        "/v1/roundtrip",
        headers={"X-Request-ID": "req-abc-123"},
        json={"signal": [1.0, 2.0, 3.0, 4.0]},
    )
    assert response.headers["X-Request-ID"] == "req-abc-123"
    body = response.json()
    assert body["request_id"] == "req-abc-123"
    assert body["success"] is True
    assert body["error"] is None
    assert body["meta"]["processing_location"]["version"] == "1.0.0"


def test_generated_request_id_when_header_absent(client: TestClient) -> None:
    response = client.post(
        "/v1/stft", json={"signal": [0.0, 1.0, 0.0]}
    )
    rid = response.json()["request_id"]
    assert isinstance(rid, str) and len(rid) >= 16
    assert response.headers["X-Request-ID"] == rid


def test_roundtrip_endpoint_concrete_numbers(client: TestClient) -> None:
    x = (np.sin(np.linspace(0, 6, 64)) + 0.3 * np.arange(64)).tolist()
    body = client.post(
        "/v1/roundtrip",
        json={"signal": x, "nperseg": 16, "hop": 8},
    ).json()
    assert body["success"] is True
    data = body["data"]
    assert data["length"] == 64
    assert data["n_frames"] >= 5
    assert data["error"]["length_preserved"] is True
    assert data["error"]["max_abs_error"] < 1e-9
    assert data["error"]["relative_rms_error"] < 1e-9
    # The returned signal numerically equals the request signal.
    np.testing.assert_allclose(
        data["signal"], x, rtol=1e-9, atol=1e-9
    )


def test_odd_window_roundtrip_endpoint(client: TestClient) -> None:
    x = [1.0, 0.0, -1.0, 0.0, 0.5, -0.5, 0.25]
    body = client.post(
        "/v1/roundtrip",
        json={"signal": x, "nperseg": 5, "hop": 2},
    ).json()
    assert body["success"] is True
    assert body["data"]["length"] == 7
    assert body["data"]["error"]["max_abs_error"] < 1e-9


def test_stft_then_istft_endpoint_roundtrip(client: TestClient) -> None:
    x = np.linspace(-1, 1, 30).tolist()
    spec_body = client.post(
        "/v1/stft",
        json={"signal": x, "nperseg": 8, "hop": 3},
    ).json()
    frames = spec_body["data"]["frames"]
    assert spec_body["data"]["n_frames"] == len(frames)
    assert spec_body["data"]["n_freq_bins"] == 5
    positions = spec_body["data"]["frame_positions"]
    assert positions[0] == {
        "frame_index": 0, "start_sample": -4, "center_sample": 0,
    }
    back = client.post(
        "/v1/istft",
        json={
            "spectrum": frames, "nperseg": 8, "hop": 3,
            "signal_length": 30,
        },
    ).json()
    assert back["success"] is True
    assert back["data"]["length"] == 30
    np.testing.assert_allclose(back["data"]["signal"], x, rtol=1e-9, atol=1e-9)


def test_boundary_pulse_roundtrip(client: TestClient) -> None:
    for pulse in ([1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]):
        body = client.post(
            "/v1/roundtrip",
            json={"signal": pulse, "nperseg": 8, "hop": 3},
        ).json()
        assert body["success"] is True
        np.testing.assert_allclose(
            body["data"]["signal"], pulse, atol=1e-9, rtol=1e-9
        )


def test_signal_shorter_than_one_window_roundtrip(client: TestClient) -> None:
    body = client.post(
        "/v1/roundtrip",
        json={"signal": [0.7, -0.2], "nperseg": 8, "hop": 4},
    ).json()
    assert body["success"] is True
    assert body["data"]["length"] == 2
    np.testing.assert_allclose(
        body["data"]["signal"], [0.7, -0.2], atol=1e-9
    )


def test_validate_endpoint_reports_nola_failure_category(client: TestClient) -> None:
    # hop >= nperseg is rejected as a parameter error before NOLA.
    body = client.post(
        "/v1/validate", json={"nperseg": 8, "hop": 8}
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMETER"
    assert body["error"]["stage"] == "parameters"
    assert body["request_id"]

    # A pathological sparse window violates NOLA.
    sparse = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    body = client.post(
        "/v1/validate", json={"nperseg": 8, "hop": 4, "window": sparse}
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOLA_VIOLATION"
    assert body["error"]["stage"] == "nola_check"
    assert body["error"]["details"]["zero_bin_count"] >= 1


def test_validate_endpoint_accepts_reconstructable(client: TestClient) -> None:
    body = client.post(
        "/v1/validate", json={"nperseg": 8, "hop": 3}
    ).json()
    assert body["success"] is True
    assert body["data"]["reconstructable"] is True
    assert body["data"]["condition"] == "NOLA"
    assert body["data"]["diagnostics"]["min_denominator"] > 0


def test_istft_rejects_mismatched_spectrum_shape(client: TestClient) -> None:
    good = client.post(
        "/v1/stft", json={"signal": list(range(16)), "nperseg": 8, "hop": 4}
    ).json()["data"]["frames"]
    broken = [frame[:-1] for frame in good]  # drop Nyquist bin
    body = client.post(
        "/v1/istft",
        json={"spectrum": broken, "nperseg": 8, "hop": 4, "signal_length": 16},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "SPECTRUM_SHAPE_MISMATCH"
    assert body["error"]["details"]["expected_freq_bins"] == 5
    assert body["error"]["details"]["nfft"] == 8


def test_istft_rejects_ragged_frames(client: TestClient) -> None:
    frames = [
        [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
        [[0.0, 0.0], [0.0, 0.0]],
    ]
    body = client.post(
        "/v1/istft",
        json={"spectrum": frames, "nperseg": 8, "hop": 4},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "SPECTRUM_SHAPE_MISMATCH"
    assert body["error"]["details"]["frame_index"] == 1


def test_istft_rejects_uncovered_length(client: TestClient) -> None:
    frames = client.post(
        "/v1/stft", json={"signal": list(range(8)), "nperseg": 8, "hop": 4}
    ).json()["data"]["frames"]
    body = client.post(
        "/v1/istft",
        json={"spectrum": frames, "nperseg": 8, "hop": 4, "signal_length": 50},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNCOVERED_SAMPLES"
    assert body["error"]["stage"] == "ola_normalize"
    assert body["error"]["details"]["requested_length"] == 50


def test_istft_rejects_non_real_dc(client: TestClient) -> None:
    frames = client.post(
        "/v1/stft", json={"signal": list(range(16)), "nperseg": 8, "hop": 4}
    ).json()["data"]["frames"]
    frames[1][0][1] = 1.0  # imaginary DC component
    body = client.post(
        "/v1/istft",
        json={"spectrum": frames, "nperseg": 8, "hop": 4, "signal_length": 16},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "ASYMMETRIC_SPECTRUM"
    assert body["error"]["details"]["bin"] == "dc"


def test_schema_validation_error_envelope(client: TestClient) -> None:
    body = client.post("/v1/stft", json={"signal": "not-a-list"}).json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["stage"] == "decode"


def test_empty_signal_rejected_with_category(client: TestClient) -> None:
    body = client.post("/v1/stft", json={"signal": []}).json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMETER"


def test_signal_over_limit_rejected(client: TestClient) -> None:
    body = client.post(
        "/v1/stft", json={"signal": [0.0] * 100_001}
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_PARAMETER"


# ------------------------------------------------------------- streaming

def test_streaming_analyze_synthesize_session_roundtrip(client: TestClient) -> None:
    rng = np.random.default_rng(9)
    x = rng.standard_normal(35).tolist()
    created = client.post(
        "/v1/streams", json={"direction": "analyze", "nperseg": 8, "hop": 3}
    ).json()["data"]
    sid = created["session_id"]
    assert created["n_freq_bins"] == 5

    all_frames = []
    part1 = client.post(
        f"/v1/streams/{sid}/analyze", json={"samples": x[:10]}
    ).json()
    assert part1["data"]["finished"] is False
    all_frames.extend(part1["data"]["frames"])
    part2 = client.post(
        f"/v1/streams/{sid}/analyze", json={"samples": x[10:], "finish": True}
    ).json()
    all_frames.extend(part2["data"]["frames"])
    assert part2["data"]["finished"] is True
    assert part2["data"]["input_samples_total"] == 35

    synth_session = client.post(
        "/v1/streams", json={"direction": "synthesize", "nperseg": 8, "hop": 3}
    ).json()["data"]["session_id"]
    for frame in all_frames[:-1]:
        resp = client.post(
            f"/v1/streams/{synth_session}/synthesize",
            json={"frame_index": frame["frame_index"], "bins": frame["bins"]},
        ).json()
        assert resp["data"]["finished"] is False
    last = all_frames[-1]
    final = client.post(
        f"/v1/streams/{synth_session}/synthesize",
        json={
            "frame_index": last["frame_index"], "bins": last["bins"],
            "finish": True, "signal_length": 35,
        },
    ).json()
    assert final["data"]["finished"] is True
    np.testing.assert_allclose(final["data"]["signal"], x, rtol=1e-9, atol=1e-9)

    # Session was closed on finish -> 404 envelope.
    gone = client.post(
        f"/v1/streams/{sid}/analyze", json={"samples": [1.0]}
    ).json()
    assert gone["error"]["code"] == "SESSION_NOT_FOUND"


def test_stream_frame_index_locations(client: TestClient) -> None:
    created = client.post(
        "/v1/streams", json={"direction": "analyze", "nperseg": 8, "hop": 3}
    ).json()["data"]
    sid = created["session_id"]
    resp = client.post(
        f"/v1/streams/{sid}/analyze",
        json={"samples": [0.0] * 10, "finish": True},
    ).json()["data"]
    for frame in resp["frames"]:
        m = frame["frame_index"]
        assert frame["start_sample"] == 3 * m - 4
        assert frame["center_sample"] == 3 * m


def test_stream_direction_conflict(client: TestClient) -> None:
    sid = client.post(
        "/v1/streams", json={"direction": "analyze"}
    ).json()["data"]["session_id"]
    body = client.post(
        f"/v1/streams/{sid}/synthesize",
        json={"frame_index": 0, "bins": [[0.0, 0.0]] * 5},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "SESSION_DIRECTION_CONFLICT"


def test_stream_out_of_order_frame(client: TestClient) -> None:
    sid = client.post(
        "/v1/streams", json={"direction": "synthesize"}
    ).json()["data"]["session_id"]
    body = client.post(
        f"/v1/streams/{sid}/synthesize",
        json={"frame_index": 2, "bins": [[0.0, 0.0]] * 5},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "FRAME_SEQUENCE_ERROR"
    assert body["error"]["details"] == {"expected": 0, "got": 2}


def test_stream_bin_count_mismatch(client: TestClient) -> None:
    sid = client.post(
        "/v1/streams", json={"direction": "synthesize", "nperseg": 8, "hop": 4}
    ).json()["data"]["session_id"]
    body = client.post(
        f"/v1/streams/{sid}/synthesize",
        json={"frame_index": 0, "bins": [[0.0, 0.0]] * 4},
    ).json()
    assert body["success"] is False
    assert body["error"]["code"] == "SPECTRUM_SHAPE_MISMATCH"
    assert body["error"]["details"]["expected_bins"] == 5
