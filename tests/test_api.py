"""End-to-end HTTP API tests (FastAPI TestClient)."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from sae.api import create_app


@pytest.fixture()
def client(service):
    return TestClient(create_app(service=service))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_full_flow_over_http(client):
    mid = client.post("/v1/messages", json={}).json()["message_id"]
    parts = [b"hello ", b"segmented ", b"world"]
    for seq, part in enumerate(parts):
        r = client.post(
            f"/v1/messages/{mid}/chunks",
            json={"seq": seq, "final": seq == len(parts) - 1,
                  "plaintext_b64": _b64(part)},
        )
        assert r.status_code == 201, r.text
        assert r.json()["replayed"] is False

    # Plaintext must not be available before finalize.
    r = client.get(f"/v1/messages/{mid}/plaintext")
    assert r.status_code == 409
    assert r.json()["error"]["category"] == "message_state_conflict"

    r = client.post(f"/v1/messages/{mid}/finalize")
    assert r.status_code == 200
    assert r.json()["size"] == len(b"hello segmented world")

    r = client.get(f"/v1/messages/{mid}/plaintext")
    assert r.status_code == 200
    assert base64.b64decode(r.json()["plaintext_b64"]) == b"hello segmented world"


def test_request_id_echoed_and_audited(client, service):
    mid = client.post("/v1/messages", json={},
                      headers={"X-Request-ID": "trace-123"}).json()["message_id"]
    r = client.post(f"/v1/messages/{mid}/chunks",
                    json={"seq": 0, "final": True, "plaintext_b64": _b64(b"x")},
                    headers={"X-Request-ID": "trace-124"})
    assert r.headers["x-request-id"] == "trace-124"
    events = service.audit.for_message(mid)
    by_event = {e["event"]: e for e in events}
    assert by_event["message_created"]["request_id"] == "trace-123"
    assert by_event["chunk_accepted"]["request_id"] == "trace-124"


def test_unknown_message_is_404(client):
    r = client.get(f"/v1/messages/{'00' * 16}")
    assert r.status_code == 404
    assert r.json()["error"]["category"] == "not_found"


def test_incomplete_stream_error_category(client):
    mid = client.post("/v1/messages", json={}).json()["message_id"]
    client.post(f"/v1/messages/{mid}/chunks",
                json={"seq": 0, "final": False, "plaintext_b64": _b64(b"x")})
    r = client.post(f"/v1/messages/{mid}/finalize")
    assert r.status_code == 409
    body = r.json()
    assert body["error"]["category"] == "incomplete_stream"
    assert body["request_id"]


def test_nonce_reuse_conflict_over_http(client):
    mid = client.post("/v1/messages", json={}).json()["message_id"]
    client.post(f"/v1/messages/{mid}/chunks",
                json={"seq": 0, "final": True, "plaintext_b64": _b64(b"A")})
    r = client.post(f"/v1/messages/{mid}/chunks",
                    json={"seq": 0, "final": True, "plaintext_b64": _b64(b"B")})
    assert r.status_code == 409
    assert r.json()["error"]["category"] == "nonce_reuse_conflict"


def test_audit_endpoint_contains_no_plaintext(client):
    secret = b"top secret chunk content"
    mid = client.post("/v1/messages", json={}).json()["message_id"]
    client.post(f"/v1/messages/{mid}/chunks",
                json={"seq": 0, "final": True, "plaintext_b64": _b64(secret)})
    client.post(f"/v1/messages/{mid}/finalize")
    r = client.get(f"/v1/messages/{mid}/audit")
    assert r.status_code == 200
    raw = r.text
    assert "top secret" not in raw
    assert _b64(secret) not in raw
    decisions = {e["decision"] for e in r.json()["events"]}
    assert decisions == {"accept"}
