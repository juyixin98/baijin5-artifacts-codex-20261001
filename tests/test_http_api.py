"""HTTP integration tests: real ASGI stack via FastAPI's TestClient (httpx).

These assert concrete status codes, error categories and correlation-id
propagation - not merely that endpoints are callable.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from scram_auth.app import create_app
from scram_auth.client import ScramClientStateMachine
from scram_auth.verifiers import build_verifier

from ._oracle_stdlib import tamper


@pytest.fixture
def client(config, audit) -> TestClient:
    app = create_app(config, audit=audit)
    app.state.repository.upsert("user", build_verifier("pencil"))
    return TestClient(app)


def _authenticate(http: TestClient, username: str = "user", password: str = "pencil", request_id: str = "req-http-0001"):
    scram_client = ScramClientStateMachine(username, password)
    first = scram_client.client_first_message()
    r1 = http.post(
        "/v1/auth/scram/first",
        json={"message": first},
        headers={"X-Request-ID": request_id},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1["request_id"] == request_id
    assert r1.headers["x-request-id"] == request_id
    final = scram_client.handle_server_first(body1["server_first"])
    r2 = http.post(
        "/v1/auth/scram/final",
        json={"session_id": body1["session_id"], "message": final},
        headers={"X-Request-ID": request_id},
    )
    assert r2.status_code == 200, r2.text
    scram_client.handle_server_final(r2.json()["server_final"])
    return scram_client, r2.json()


class TestHappyPath:
    def test_healthz_reports_scope_and_version(self, client: TestClient) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["mechanism"] == "SCRAM-SHA-256"
        assert payload["channel_binding_scope"] == ["n", "y"]
        assert payload["version"] == "1.0.0"
        assert "location" in payload and "request_id" in payload

    def test_full_authentication_over_http(self, client: TestClient) -> None:
        scram_client, body = _authenticate(client)
        assert scram_client.phase.value == "verified"
        assert body["success"] is True and body["username"] == "user"
        assert body["server_final"].startswith("v=")

    def test_request_id_is_minted_when_absent_or_invalid(self, client: TestClient) -> None:
        scram_client = ScramClientStateMachine("user", "pencil")
        resp = client.post("/v1/auth/scram/first", json={"message": scram_client.client_first_message()})
        assert resp.status_code == 200
        minted = resp.json()["request_id"]
        assert len(minted) == 32 and minted.isalnum()

        scram_client2 = ScramClientStateMachine("user", "pencil")
        resp2 = client.post(
            "/v1/auth/scram/first",
            json={"message": scram_client2.client_first_message()},
            headers={"X-Request-ID": "bad id!!"},
        )
        assert resp2.json()["request_id"] != "bad id!!"


class TestErrorEnvelopes:
    def test_tampered_proof_returns_401_invalid_proof(self, client: TestClient) -> None:
        scram_client = ScramClientStateMachine("user", "pencil")
        r1 = client.post("/v1/auth/scram/first", json={"message": scram_client.client_first_message()})
        body1 = r1.json()
        final = scram_client.handle_server_first(body1["server_first"])
        head, _, proof = final.partition(",p=")
        forged = f"{head},p={tamper(proof)}"
        r2 = client.post(
            "/v1/auth/scram/final",
            json={"session_id": body1["session_id"], "message": forged},
        )
        assert r2.status_code == 401
        error = r2.json()["error"]
        assert error["category"] == "invalid-proof"
        assert "detail" in error and isinstance(error["message"], str)

    def test_unknown_session_is_404_session_not_found(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/auth/scram/final",
            json={"session_id": "0" * 32, "message": "c=biws,r=x,p=AA=="},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["category"] == "session-not-found"

    def test_protocol_violation_is_400(self, client: TestClient) -> None:
        # Duplicate attribute in the bare message.
        resp = client.post("/v1/auth/scram/first", json={"message": "n,,n=user,n=other,r=abcdefghijklmnop"})
        assert resp.status_code == 400
        assert resp.json()["error"]["category"] == "protocol-violation"

    def test_second_final_after_failure_is_410_session_reuse(self, client: TestClient) -> None:
        scram_client = ScramClientStateMachine("user", "pencil")
        r1 = client.post("/v1/auth/scram/first", json={"message": scram_client.client_first_message()})
        session_id = r1.json()["session_id"]
        final = scram_client.handle_server_first(r1.json()["server_first"])
        head, _, proof = final.partition(",p=")
        client.post(
            "/v1/auth/scram/final",
            json={"session_id": session_id, "message": f"{head},p={tamper(proof)}"},
        )
        replay = client.post(
            "/v1/auth/scram/final",
            json={"session_id": session_id, "message": final},
        )
        assert replay.status_code == 410
        assert replay.json()["error"]["category"] == "session-reuse"

    def test_nonce_replay_returns_401_nonce_replay(self, client: TestClient) -> None:
        c1 = ScramClientStateMachine("user", "pencil", client_nonce="replay-nonce-0123456789abcdef")
        c2 = ScramClientStateMachine("user", "pencil", client_nonce="replay-nonce-0123456789abcdef")
        first_ok = client.post("/v1/auth/scram/first", json={"message": c1.client_first_message()})
        assert first_ok.status_code == 200
        replay = client.post("/v1/auth/scram/first", json={"message": c2.client_first_message()})
        assert replay.status_code == 401
        assert replay.json()["error"]["category"] == "nonce-replay"


class TestRateLimiting:
    def test_repeated_protocol_abuse_eventually_returns_429(self, client: TestClient) -> None:
        statuses = []
        for _ in range(25):
            resp = client.post(
                "/v1/auth/scram/first",
                json={"message": "n,,n=user,n=dup,r=abcdefghijklmnop"},
            )
            statuses.append(resp.status_code)
        assert 429 in statuses
        assert statuses[-1] == 429


class TestAuditTrail:
    def test_events_are_correlated_and_contain_no_secrets(self, client: TestClient, config) -> None:
        _authenticate(client, request_id="req-audit-link")
        # The audit sink retained parsed events in process.
        events = client.app.state.audit.events
        linked = [e for e in events if e["request_id"] == "req-audit-link"]
        assert len(linked) >= 4  # http_request x2, server_first_sent, success
        steps = {e["step"] for e in linked}
        event_names = {e["event"] for e in linked}
        assert "http_request" in event_names
        assert "/v1/auth/scram/first" in steps and "/v1/auth/scram/final" in steps
        assert any(e["outcome"] == "success" for e in linked)
        assert {e["outcome"] for e in linked} >= {"info", "success"}
        # Every event identifies component/version/location.
        for event in linked:
            assert event["component"] == "scram-auth-local"
            assert event["version"] == "1.0.0"
            assert event["location"].startswith("src/scram_auth/")

        # The on-disk JSONL must not contain the plaintext password or proof.
        with open(config.server.audit_log_path, encoding="utf-8") as fh:
            raw = fh.read()
        assert "pencil" not in raw
        for line in raw.splitlines():
            json.loads(line)  # every line is valid JSON

    def test_failure_is_logged_under_its_own_category(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/auth/scram/final",
            json={"session_id": "f" * 32, "message": "c=biws,r=x,p=AA=="},
        )
        assert resp.status_code == 404
        events = client.app.state.audit.events
        failures = [e for e in events if e.get("failure", {}).get("category") == "session-not-found"]
        assert failures, "expected a session-not-found audit event"


class TestChannelBindingHeader:
    def test_cert_hash_in_non_plus_mode_is_501(self, client: TestClient) -> None:
        scram_client = ScramClientStateMachine("user", "pencil")
        resp = client.post(
            "/v1/auth/scram/first",
            json={"message": scram_client.client_first_message()},
            headers={"X-Tls-Server-End-Point-Sha256": "aa" * 32},
        )
        assert resp.status_code == 501
        assert resp.json()["error"]["category"] == "unsupported-channel-binding"

    def test_malformed_cert_hash_hex_is_401_mismatch(self, plus_config, audit) -> None:
        app = create_app(plus_config, audit=audit)
        app.state.repository.upsert("user", build_verifier("pencil"))
        http = TestClient(app)
        scram_client = ScramClientStateMachine(
            "user",
            "pencil",
            channel_binding="tls-server-end-point",
            tls_endpoint_hash=bytes.fromhex("aa" * 32),
        )
        resp = http.post(
            "/v1/auth/scram/first",
            json={"message": scram_client.client_first_message()},
            headers={"X-Tls-Server-End-Point-Sha256": "not-hex!!"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["category"] == "channel-bindings-dont-match"

    def test_plus_exchange_succeeds_over_http_with_header(self, plus_config, audit) -> None:
        app = create_app(plus_config, audit=audit)
        app.state.repository.upsert("user", build_verifier("pencil"))
        http = TestClient(app)
        digest = bytes.fromhex("aa" * 32)
        scram_client = ScramClientStateMachine(
            "user", "pencil", channel_binding="tls-server-end-point", tls_endpoint_hash=digest
        )
        r1 = http.post(
            "/v1/auth/scram/first",
            json={"message": scram_client.client_first_message()},
            headers={"X-Tls-Server-End-Point-Sha256": "aa" * 32},
        )
        assert r1.status_code == 200, r1.text
        final = scram_client.handle_server_first(r1.json()["server_first"])
        r2 = http.post(
            "/v1/auth/scram/final",
            json={"session_id": r1.json()["session_id"], "message": final},
        )
        assert r2.status_code == 200, r2.text
        scram_client.handle_server_final(r2.json()["server_final"])
        assert scram_client.phase.value == "verified"


class TestUncertainObservations:
    def test_invalid_correlation_id_is_reassigned_and_recorded_as_uncertain(self, client: TestClient) -> None:
        scram_client = ScramClientStateMachine("user", "pencil")
        client.post(
            "/v1/auth/scram/first",
            json={"message": scram_client.client_first_message()},
            headers={"X-Request-ID": "bad id!!"},
        )
        uncertain = [e for e in client.app.state.audit.events if e["outcome"] == "uncertain"]
        assert uncertain and uncertain[0]["event"] == "correlation_id_reassigned"
        assert "minted a replacement" in uncertain[0]["uncertain"][0]
