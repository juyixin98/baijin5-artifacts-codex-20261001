"""End-to-end API tests: contracts, error categories, param versioning."""

import numpy as np
import pytest
import scipy.signal
from fastapi.testclient import TestClient

from app.config import Settings
from app.dsp.cascade import SosCascadeFilter
from app.dsp.coefficients import normalize_and_validate
from app.main import create_app
from app.state.store import StreamStore

LIMITS = dict(max_sections=32, pole_radius_limit=1.0)


@pytest.fixture()
def client():
    store = StreamStore(Settings(max_streams=4, max_block_samples=256))
    return TestClient(create_app(store))


@pytest.fixture()
def sos():
    return normalize_and_validate(
        scipy.signal.butter(4, 0.2, output="sos"), **LIMITS
    ).sections


def _create(client, sos, **overrides):
    payload = {
        "sample_rate": 48000.0,
        "num_channels": 2,
        "coefficients": np.asarray(sos).tolist(),
        **overrides,
    }
    resp = client.post("/streams", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_create_and_process_roundtrip(client, sos, runlog):
    info = _create(client, sos)
    assert info["param_version"] == 1
    assert info["num_sections"] == sos.shape[0]

    rng = np.random.default_rng(7)
    x = rng.standard_normal((64, 2))
    resp = client.post(f"/streams/{info['stream_id']}/blocks",
                       json={"samples": x.tolist()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    got = np.asarray(body["samples"])

    expected = SosCascadeFilter(sos, 2).process_block(x)
    err = float(np.max(np.abs(got - expected)))
    runlog("api_roundtrip", run_id_header=resp.headers.get("x-run-id"),
           max_abs_err=err, tolerance=1e-12,
           rationale="HTTP round-trip must match local cascade")
    assert err < 1e-12
    assert body["samples_processed"] == 64


def test_version_pin_conflict_returns_409(client, sos, runlog):
    info = _create(client, sos)
    resp = client.post(
        f"/streams/{info['stream_id']}/blocks",
        json={"samples": [[0.0, 0.0]], "param_version": 99},
    )
    runlog("version_conflict", status=resp.status_code,
           body=resp.json(), rationale="stale param_version must be 409")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["category"] == "state_conflict"
    assert err["run_id"]


def test_coefficient_update_bumps_version_and_enforces_expected(client, sos):
    info = _create(client, sos)
    sid = info["stream_id"]
    new_sos = normalize_and_validate(
        scipy.signal.butter(2, 0.3, output="sos"), **LIMITS
    ).sections

    stale = client.put(
        f"/streams/{sid}/coefficients",
        json={"coefficients": new_sos.tolist(), "expected_version": 42},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["category"] == "state_conflict"

    ok = client.put(
        f"/streams/{sid}/coefficients",
        json={"coefficients": new_sos.tolist(), "expected_version": 1,
              "transient": "reset_state"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["param_version"] == 2

    # after reset_state switch, stream behaves as a fresh filter
    x = np.random.default_rng(3).standard_normal((32, 2))
    resp = client.post(f"/streams/{sid}/blocks",
                       json={"samples": x.tolist(), "param_version": 2})
    assert resp.status_code == 200
    got = np.asarray(resp.json()["samples"])
    expected = SosCascadeFilter(new_sos, 2).process_block(x)
    assert np.allclose(got, expected, atol=1e-12)


def test_invalid_coefficients_return_422_input_error(client, runlog):
    payload = {
        "sample_rate": 48000.0,
        "num_channels": 1,
        "coefficients": [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0]],  # a0 = 0
    }
    resp = client.post("/streams", json=payload)
    runlog("bad_coeffs", status=resp.status_code, body=resp.json())
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["category"] == "input_error"
    assert err["code"] == "invalid_coefficients"


def test_unstable_coefficients_return_422(client):
    payload = {
        "sample_rate": 48000.0,
        "num_channels": 1,
        "coefficients": [[1.0, 0.0, 0.0, 1.0, -2.0, 0.0]],  # pole at z=2
    }
    resp = client.post("/streams", json=payload)
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_unknown_stream_returns_404(client):
    resp = client.post("/streams/deadbeef/blocks",
                       json={"samples": [[0.0, 0.0]]})
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "not_found"


def test_oversized_block_returns_429_resource(client, sos, runlog):
    info = _create(client, sos)
    big = np.zeros((257, 2))  # fixture caps blocks at 256 samples
    resp = client.post(f"/streams/{info['stream_id']}/blocks",
                       json={"samples": big.tolist()})
    runlog("oversized_block", status=resp.status_code)
    assert resp.status_code == 429
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_stream_limit_returns_429(client, sos):
    for _ in range(4):  # fixture caps streams at 4
        _create(client, sos)
    resp = client.post("/streams", json={
        "sample_rate": 48000.0, "num_channels": 1,
        "coefficients": np.asarray(sos).tolist(),
    })
    assert resp.status_code == 429
    assert resp.json()["error"]["category"] == "resource_exhausted"


def test_ragged_block_returns_422_input_error(client, sos):
    info = _create(client, sos)
    resp = client.post(
        f"/streams/{info['stream_id']}/blocks",
        json={"samples": [[1.0, 2.0], [3.0]]},  # ragged rows
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "input_error"


def test_delete_stream(client, sos):
    info = _create(client, sos)
    sid = info["stream_id"]
    assert client.delete(f"/streams/{sid}").status_code == 204
    assert client.get(f"/streams/{sid}").status_code == 404


def test_chunked_api_blocks_match_single_block(client, sos, runlog, rng):
    x = rng.standard_normal((96, 2))
    whole_id = _create(client, sos)["stream_id"]
    chunk_id = _create(client, sos)["stream_id"]

    r1 = client.post(f"/streams/{whole_id}/blocks",
                     json={"samples": x.tolist()})
    parts = [client.post(f"/streams/{chunk_id}/blocks",
                         json={"samples": x[i:i + 16].tolist()})
             for i in range(0, 96, 16)]
    assert all(p.status_code == 200 for p in parts)
    whole = np.asarray(r1.json()["samples"])
    chunked = np.concatenate([np.asarray(p.json()["samples"]) for p in parts])
    identical = bool(np.array_equal(whole, chunked))
    runlog("api_chunk_equiv", bitwise_identical=identical)
    assert identical
