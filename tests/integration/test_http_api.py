"""End-to-end HTTP tests against the FastAPI app (real ASGI transport)."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.api.server import RateLimiter, create_app
from app.core.crypto import CryptographyBackend
from app.core.sender import encode_message

pytestmark = pytest.mark.integration


def _client(service):
    app = create_app(service, rate_limiter=RateLimiter(max_requests=1000))
    return TestClient(app)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def test_http_happy_path_out_of_order_and_result_download(
        make_service, fixture_keys):
    plaintext = bytes((i * 3 + 7) & 0xFF for i in range(333))
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "http-1", plaintext, 50)
    svc = make_service()
    with _client(svc) as client:
        r = client.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"

        order = list(range(sealed.total_segments))
        order.reverse()
        for i in order:
            resp = client.post("/v1/segments",
                               json={"frame_b64": _b64(sealed.frames[i])},
                               headers={"x-request-id": f"rid-{i}"})
            assert resp.status_code == 200, resp.text
            assert "x-request-id" in resp.headers
        fin = client.post("/v1/streams/http-1/finalize")
        assert fin.status_code == 200 and fin.json()["complete"] is True

        result = client.get("/v1/streams/http-1/result")
        assert result.status_code == 200
        assert result.content == plaintext


def test_http_result_refused_before_completion(make_service, fixture_keys):
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "http-2", b"abcdefgh", 4)
    svc = make_service()
    with _client(svc) as client:
        client.post("/v1/segments", json={"frame_b64": _b64(sealed.frames[0])})
        result = client.get("/v1/streams/http-2/result")
        assert result.status_code == 409
        body = result.json()
        assert body["category"] == "state"
        assert body["request_id"]


def test_http_bad_base64_is_protocoding(make_service):
    svc = make_service()
    with _client(svc) as client:
        resp = client.post("/v1/segments", json={"frame_b64": "%%%notb64"})
        assert resp.status_code == 422
        assert resp.json()["category"] == "protocoding"


def test_http_tampered_segment_returns_auth_failed(make_service, fixture_keys):
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "http-3", b"abcdefghij", 4)
    raw = bytearray(sealed.frames[0])
    raw[-2] ^= 0xAA
    svc = make_service()
    with _client(svc) as client:
        resp = client.post("/v1/segments",
                           json={"frame_b64": _b64(bytes(raw))})
        assert resp.status_code == 400
        assert resp.json()["category"] == "auth_failed"


def test_http_nonce_conflict_returns_409(make_service, fixture_keys):
    first = encode_message(fixture_keys, CryptographyBackend(),
                           "http-4", b"abcdefgh", 4)
    second = encode_message(fixture_keys, CryptographyBackend(),
                            "http-4", b"Xbcdefgh", 4)
    svc = make_service()
    with _client(svc) as client:
        assert client.post("/v1/segments",
                           json={"frame_b64": _b64(first.frames[0])}
                           ).status_code == 200
        resp = client.post("/v1/segments",
                           json={"frame_b64": _b64(second.frames[0])})
        assert resp.status_code == 409
        assert resp.json()["category"] == "nonce_conflict"


def test_http_finalize_incomplete_returns_409_incomplete(
        make_service, fixture_keys):
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "http-5", b"abcdefghijkl", 4)
    svc = make_service()
    with _client(svc) as client:
        client.post("/v1/segments", json={"frame_b64": _b64(sealed.frames[0])})
        resp = client.post("/v1/streams/http-5/finalize")
        assert resp.status_code == 409
        assert resp.json()["category"] == "incomplete"
        assert resp.json()["state"]["received"] == 1
        assert resp.json()["state"]["total"] == 3


def test_http_unknown_stream_status_409(make_service):
    svc = make_service()
    with _client(svc) as client:
        assert client.get("/v1/streams/nope/status").status_code == 409


def test_http_validation_error_shape(make_service):
    svc = make_service()
    with _client(svc) as client:
        resp = client.post("/v1/streams/begin",
                           json={"message_id": "bad id!",
                                 "total_segments": 1, "total_len": 0})
        assert resp.status_code == 422
        assert resp.json()["category"] == "protocoding"


def test_http_audit_endpoint_carries_categories(make_service, fixture_keys):
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "http-6", b"abcdefgh", 4)
    svc = make_service()
    with _client(svc) as client:
        client.post("/v1/segments", json={"frame_b64": _b64(sealed.frames[0])},
                    headers={"x-request-id": "rid-audit"})
        resp = client.get("/v1/audit", params={"message_id": "http-6"})
        assert resp.status_code == 200
        events = resp.json()
        assert any(e["request_id"] == "rid-audit" for e in events)
        for e in events:
            assert "ciphertext" not in str(e["state"])


def test_http_rate_limiter_rejects_burst(make_service_local):
    # Dedicated tiny-limit app; service never reached after the limit.
    app = create_app(make_service_local,
                     rate_limiter=RateLimiter(max_requests=3, window_s=60))
    with TestClient(app) as client:
        statuses = [client.post("/v1/streams/begin",
                                json={"message_id": f"m-{i}",
                                      "total_segments": 1, "total_len": 0}
                                ).status_code for i in range(6)]
    assert statuses[:3] == [201, 201, 201]
    assert 429 in statuses[3:]


@pytest.fixture
def make_service_local(tmp_path, fixture_keys):
    from app.core.service import SegmentedAEADService
    from app.core.staging import StagingArea
    from app.db.store import Store

    def _factory():
        store = Store(tmp_path / "ratelimit.sqlite3")
        area = StagingArea(tmp_path / "rl-stage", tmp_path / "rl-rel")
        return SegmentedAEADService(store, area, fixture_keys,
                                    CryptographyBackend())
    return _factory()
