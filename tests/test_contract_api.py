"""Contract validation and FastAPI service tests, including failure
classes: every abnormal input must produce its specific error category,
never a silent 200."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from limiter import fixtures
from limiter.api import create_app
from limiter.config import ALGORITHM_CONTRACT_VERSION, LimiterConfig
from limiter.contract import ContractError, validate_block
from limiter.stream import StreamingLimiter, process_offline


# ---------- block contract ----------

def test_validate_block_rejects_1d():
    with pytest.raises(ContractError, match="bad_shape"):
        validate_block([0.1, 0.2, 0.3], channels=2)


def test_validate_block_rejects_wrong_channel_count():
    with pytest.raises(ContractError, match="bad_channels"):
        validate_block(np.zeros((8, 3)), channels=2)


def test_validate_block_rejects_non_finite():
    x = np.zeros((8, 2))
    x[3, 1] = np.nan
    with pytest.raises(ContractError, match="non_finite"):
        validate_block(x, channels=2)
    x[3, 1] = np.inf
    with pytest.raises(ContractError, match="non_finite"):
        validate_block(x, channels=2)


def test_validate_block_rejects_ragged():
    with pytest.raises(ContractError, match="bad_dtype"):
        validate_block([[0.1, 0.2], [0.3]], channels=2)


def test_stream_rejects_use_after_flush(cfg):
    lim = StreamingLimiter(cfg)
    lim.process(np.zeros((300, 2)))
    lim.flush()
    with pytest.raises(ContractError, match="stream_flushed"):
        lim.process(np.zeros((10, 2)))
    with pytest.raises(ContractError, match="stream_flushed"):
        lim.flush()


def test_config_rejects_non_sample_peak_mode():
    with pytest.raises(ValueError, match="peak_mode"):
        LimiterConfig(peak_mode="true")


# ---------- API ----------

@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["algorithm_version"] == ALGORITHM_CONTRACT_VERSION
    assert body["peak_mode"] == "sample"
    assert "numpy" in body["versions"] and "scipy" in body["versions"]


def test_limit_offline_matches_library_and_ceiling(client, cfg, tlog):
    fx = fixtures.impulse(n=2048, index=500, amplitude=2.0)
    pcm = fx.pcm()
    r = client.post("/v1/limit", json={"pcm": pcm.tolist()})
    body = r.json()
    metrics = body["metrics"]
    lib = process_offline(pcm, cfg)

    tlog(
        "api_limit_offline",
        fixture_sha256=fx.sha256(),
        status_code=r.status_code,
        frames=body["frames"],
        latency_samples=body["latency_samples"],
        output_peak=metrics["output_peak"],
        promised_ceiling=metrics["promised_ceiling"],
        ceiling_ok=metrics["ceiling_ok"],
        service_vs_library_max_diff=float(
            np.max(np.abs(np.array(body["output"]) - lib.output))
        ),
    )

    assert r.status_code == 200
    assert body["algorithm_version"] == ALGORITHM_CONTRACT_VERSION
    assert body["latency_samples"] == cfg.lookahead_samples
    assert body["frames"] == len(pcm)
    assert len(body["gain"]) == len(pcm)
    assert metrics["ceiling_ok"] is True
    assert metrics["output_peak"] <= metrics["promised_ceiling"] + 1e-12
    # Service layer must return bit-identical numbers to the library.
    assert np.array_equal(np.array(body["output"]), lib.output)
    assert np.array_equal(np.array(body["gain"]), lib.gain)


def test_limit_offline_error_categories(client):
    # NaN sample -> 422 non_finite (not a 200, not a 500). httpx/json
    # refuses to serialize NaN, so send the raw (non-strict) JSON body
    # that a lenient client could produce.
    r = client.post(
        "/v1/limit",
        content=b'{"pcm": [[0.0, 0.0], [NaN, 0.0]]}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "non_finite"

    # 3-channel block against 2-channel default config -> 422 bad_channels
    r = client.post("/v1/limit", json={"pcm": [[0.0, 0.0, 0.0]] * 4})
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "bad_channels"

    # illegal config -> 422 bad_config
    r = client.post("/v1/limit", json={"config": {"attack_ms": -1.0}, "pcm": [[0.0, 0.0]]})
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "bad_config"


def test_session_streaming_matches_offline(client, cfg, tlog):
    fx = fixtures.impulse(n=2048, index=500, amplitude=2.0)
    pcm = fx.pcm()
    L = cfg.lookahead_samples

    r = client.post("/v1/sessions", json={"config": {}})
    assert r.status_code == 201
    sid = r.json()["session_id"]
    assert r.json()["latency_samples"] == L

    # block 1: 1024 in -> 1024 - L out (latency is observable mid-stream)
    r1 = client.post(f"/v1/sessions/{sid}/blocks", json={"pcm": pcm[:1024].tolist()})
    b1 = r1.json()

    # block 2: another 1024 in -> 1024 out, indices continue seamlessly
    r2 = client.post(f"/v1/sessions/{sid}/blocks", json={"pcm": pcm[1024:].tolist()})
    b2 = r2.json()

    # flush: exactly the final L frames, total == input length
    rf = client.post(f"/v1/sessions/{sid}/flush")
    bf = rf.json()

    streamed = np.array(b1["pcm"] + b2["pcm"] + bf["pcm"])
    streamed_gain = np.array(b1["gain"] + b2["gain"] + bf["gain"])
    lib = process_offline(pcm, cfg)

    # use after flush -> 409, never a silent 200
    r3 = client.post(f"/v1/sessions/{sid}/blocks", json={"pcm": [[0.0, 0.0]]})

    tlog(
        "api_session_streaming",
        fixture_sha256=fx.sha256(),
        session_id=sid,
        status_codes=[r1.status_code, r2.status_code, rf.status_code],
        emitted_per_call=[b1["emitted"], b2["emitted"], bf["emitted"]],
        total_emitted=bf["frames_emitted_total"],
        streamed_vs_offline_max_diff=float(np.max(np.abs(streamed - lib.output))),
        post_flush_status=r3.status_code,
    )

    assert r1.status_code == 200 and r2.status_code == 200 and rf.status_code == 200
    assert b1["emitted"] == 1024 - L
    assert b1["start_index"] == 0
    assert b2["emitted"] == 1024
    assert b2["start_index"] == 1024 - L
    assert bf["emitted"] == L
    assert bf["frames_emitted_total"] == len(pcm)
    assert np.array_equal(streamed, lib.output)
    assert np.array_equal(streamed_gain, lib.gain)
    assert r3.status_code == 409
    assert r3.json()["detail"]["category"] == "stream_flushed"


def test_unknown_session_is_404(client):
    r = client.post("/v1/sessions/doesnotexist/blocks", json={"pcm": [[0.0, 0.0]]})
    assert r.status_code == 404
    assert r.json()["detail"]["category"] == "unknown_session"
    r = client.post("/v1/sessions/doesnotexist/flush")
    assert r.status_code == 404
