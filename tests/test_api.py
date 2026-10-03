"""End-to-end API tests through the FastAPI app: happy-path convolution
checked against the independent NumPy reference, plus every failure
category surfaced as a typed error body with a request id."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.fixtures import make_exponential_ir, make_random_signal
from app.main import create_app
from tests.reference import direct_convolve_np

B = 128


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(Settings()))


@pytest.fixture()
def session_id(client: TestClient) -> str:
    ir = make_exponential_ir(300, seed=101)
    resp = client.post(
        "/v1/sessions",
        json={
            "sample_rate": 48_000,
            "block_size": B,
            "ir": ir.tolist(),
            "swap_strategy": "restart",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def test_healthz(client: TestClient):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_session_reports_state_budget(client: TestClient):
    ir = make_exponential_ir(1000, seed=103)
    resp = client.post(
        "/v1/sessions",
        json={"sample_rate": 48_000, "block_size": 256, "ir": ir.tolist()},
        headers={"X-Request-ID": "req-create-1"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["request_id"] == "req-create-1"
    assert resp.headers["x-request-id"] == "req-create-1"
    assert body["num_partitions"] == 4  # ceil(1000 / 256)
    sb = body["state_bytes"]
    # Spectrum history dominates and is fully accounted.
    assert sb["ir_spectra"] == 4 * 257 * 16
    assert sb["input_fdl"] == 4 * 257 * 16
    assert sb["total"] == sb["ir_spectra"] + sb["input_fdl"] + sb["overlap_tail"]


def test_stream_matches_direct_convolution_end_to_end(client: TestClient, session_id: str):
    ir = make_exponential_ir(300, seed=101)
    x = make_random_signal(1000, seed=105)  # 7 full blocks + 104-sample tail
    outputs = []
    for start in range(0, len(x), B):
        chunk = x[start : start + B]
        resp = client.post(
            f"/v1/sessions/{session_id}/blocks",
            json={"samples": chunk.tolist(), "final": start + B >= len(x)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["output_length"] == B
        assert body["request_id"]
        outputs.append(body["output"])
    resp = client.post(f"/v1/sessions/{session_id}/flush")
    assert resp.status_code == 200, resp.text
    flush = resp.json()
    y = np.concatenate([np.asarray(o) for o in outputs] + [np.asarray(flush["tail"])])

    ref = direct_convolve_np(x, ir)
    assert flush["total_input_samples"] == len(x)
    assert flush["total_output_samples"] == len(ref)
    assert y.shape == ref.shape
    np.testing.assert_allclose(y, ref, atol=1e-9)


def test_block_size_mismatch_category(client: TestClient, session_id: str):
    resp = client.post(
        f"/v1/sessions/{session_id}/blocks",
        json={"samples": [0.0] * 100},
    )
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "BLOCK_SIZE_MISMATCH"
    assert err["request_id"]


def test_unknown_session_is_undecidable_category(client: TestClient):
    resp = client.post(
        "/v1/sessions/does-not-exist/blocks",
        json={"samples": [0.0] * B},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"


def test_double_flush_rejected(client: TestClient, session_id: str):
    client.post(f"/v1/sessions/{session_id}/blocks", json={"samples": [0.0] * B})
    assert client.post(f"/v1/sessions/{session_id}/flush").status_code == 200
    resp = client.post(f"/v1/sessions/{session_id}/flush")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SESSION_ALREADY_FLUSHED"


def test_input_after_final_block_rejected(client: TestClient, session_id: str):
    client.post(
        f"/v1/sessions/{session_id}/blocks",
        json={"samples": [0.0] * 64, "final": True},
    )
    resp = client.post(
        f"/v1/sessions/{session_id}/blocks", json={"samples": [0.0] * B}
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "INPUT_AFTER_FINAL_BLOCK"


def test_state_budget_exceeded_category(client: TestClient):
    ir = make_exponential_ir(3000, seed=107)
    resp = client.post(
        "/v1/sessions",
        json={
            "sample_rate": 48_000,
            "block_size": B,
            "ir": ir.tolist(),
            "max_state_bytes": 10_000,
        },
    )
    assert resp.status_code == 413
    err = resp.json()["error"]
    assert err["code"] == "STATE_BUDGET_EXCEEDED"
    assert err["detail"]["projected_bytes"] > err["detail"]["budget_bytes"]


def test_schema_validation_category(client: TestClient):
    resp = client.post(
        "/v1/sessions",
        json={"sample_rate": -1, "block_size": B, "ir": [1.0]},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SCHEMA_VALIDATION"


def test_ir_swap_via_api(client: TestClient, session_id: str):
    ir_new = make_exponential_ir(200, seed=109)
    resp = client.post(
        f"/v1/sessions/{session_id}/ir",
        json={"ir": ir_new.tolist(), "strategy": "restart"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "restart"
    assert body["ir_length"] == 200
    # Probe: impulse must now reproduce the new IR.
    impulse = np.zeros(2 * B)
    impulse[0] = 1.0
    client.post(f"/v1/sessions/{session_id}/blocks", json={"samples": [0.0] * B})
    outs = []
    for start in range(0, 2 * B, B):
        r = client.post(
            f"/v1/sessions/{session_id}/blocks",
            json={"samples": impulse[start : start + B].tolist()},
        )
        outs.append(r.json()["output"])
    tail = client.post(f"/v1/sessions/{session_id}/flush").json()["tail"]
    y = np.concatenate([np.asarray(o) for o in outs] + [np.asarray(tail)])
    np.testing.assert_allclose(y[: len(ir_new)], ir_new, atol=1e-9)


def test_session_state_endpoint(client: TestClient, session_id: str):
    client.post(f"/v1/sessions/{session_id}/blocks", json={"samples": [1.0] * B})
    resp = client.get(f"/v1/sessions/{session_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["blocks_processed"] == 1
    assert body["total_input_samples"] == B
    assert body["state_bytes"]["total"] > 0
    assert body["request_id"]


def test_delete_session(client: TestClient, session_id: str):
    assert client.delete(f"/v1/sessions/{session_id}").status_code == 204
    assert client.get(f"/v1/sessions/{session_id}").status_code == 404
