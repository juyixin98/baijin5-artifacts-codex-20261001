"""Defensive-edge tests: size limits, session cap/TTL, authzid, proof shape."""
from __future__ import annotations

import time

import pytest

from scram_auth.client import ScramClientStateMachine
from scram_auth.errors import (
    ChannelBindingMismatch,
    FailureCategory,
    InvalidProof,
    ProtocolViolation,
    SessionExpired,
    UnsupportedChannelBinding,
)
from scram_auth.server import ScramServerStateMachine
from scram_auth.verifiers import build_verifier


def _start(server, username: str = "alice", password: str = "pencil", nonce: str | None = None, **kw):
    client = ScramClientStateMachine(username, password, client_nonce=nonce)
    challenge = server.receive_client_first(client.client_first_message(), request_id="req-edge", **kw)
    return client, challenge


class TestEnforcement:
    def test_oversized_client_first_rejected(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        huge_nonce = "a" * (config.limits.max_message_bytes + 10)
        client = ScramClientStateMachine("alice", "pencil", client_nonce=huge_nonce)
        with pytest.raises(ProtocolViolation) as exc:
            server.receive_client_first(client.client_first_message(), request_id="req-big")
        assert exc.value.detail["limit"] == config.limits.max_message_bytes

    def test_oversized_client_final_rejected(self, config, repository, audit) -> None:
        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(config, repository, audit)
        _, challenge = _start(server)
        with pytest.raises(ProtocolViolation):
            server.receive_client_final(
                "c=" + "Q" * 5000 + ",r=x,p=AA==",
                session_id=challenge.session_id,
                request_id="req-big-final",
            )

    def test_session_cap_is_enforced(self, config, repository, audit) -> None:
        object.__setattr__(
            config,
            "session",
            type(config.session)(ttl_seconds=config.session.ttl_seconds, max_active=2),
        )
        server = ScramServerStateMachine(config, repository, audit)
        for i in range(2):
            client = ScramClientStateMachine("u", "p", client_nonce=f"cap-nonce-{i}-0123456789abcd")
            server.receive_client_first(client.client_first_message(), request_id=f"req-cap-{i}")
        client3 = ScramClientStateMachine("u", "p", client_nonce="cap-nonce-z-0123456789abcd")
        with pytest.raises(ProtocolViolation) as exc:
            server.receive_client_first(client3.client_first_message(), request_id="req-cap-2")
        assert exc.value.detail["cap"] == 2

    def test_expired_session_raises_and_is_reaped(self, config, repository, audit) -> None:
        # TTL of effectively zero: every session is stale by final time.
        object.__setattr__(config.session, "ttl_seconds", 0)
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("alice", "pencil")
        challenge = server.receive_client_first(client.client_first_message(), request_id="req-ttl")
        time.sleep(0.01)
        final = client.handle_server_first(challenge.message)
        with pytest.raises(SessionExpired):
            server.receive_client_final(final, session_id=challenge.session_id, request_id="req-ttl")
        assert server.is_terminal(challenge.session_id) is None

    def test_authzid_is_rejected(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        with pytest.raises(ProtocolViolation) as exc:
            server.receive_client_first(
                "n,a=bob,n=alice,r=nonce-with-authzid-0123456789",
                request_id="req-authzid",
            )
        assert exc.value.detail["has_authzid"] is True

    def test_username_length_cap(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        long_name = "a" * (config.limits.max_username_chars + 1)
        client = ScramClientStateMachine(long_name, "pencil")
        with pytest.raises(ProtocolViolation, match="username"):
            server.receive_client_first(client.client_first_message(), request_id="req-name")

    def test_nonce_too_long_rejected(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("alice", "pencil", client_nonce="x" * 129)
        with pytest.raises(ProtocolViolation, match="too long"):
            server.receive_client_first(client.client_first_message(), request_id="req-nlen")

    def test_wrong_length_proof_is_protocol_violation(self, config, repository, audit) -> None:
        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(config, repository, audit)
        client, challenge = _start(server)
        client.handle_server_first(challenge.message)
        malformed = f"c=biws,r={_extract_nonce(challenge.message)},p=AA=="
        with pytest.raises(ProtocolViolation, match="proof must be"):
            server.receive_client_final(malformed, session_id=challenge.session_id, request_id="req-prooflen")

    def test_plus_without_delivered_hash_is_mismatch(self, plus_config, repository, audit) -> None:
        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(plus_config, repository, audit)
        client = ScramClientStateMachine(
            "alice", "pencil", channel_binding="tls-server-end-point", tls_endpoint_hash=b"\xcc" * 32
        )
        # Application parsed the PLUS header but the transport delivered no hash.
        with pytest.raises(ChannelBindingMismatch):
            server.receive_client_first(
                client.client_first_message(), request_id="req-nohash", tls_endpoint_hash=None
            )


def _extract_nonce(server_first: str) -> str:
    return server_first.split(",", 1)[0][2:]


class TestServerVerifierProvisioning:
    def test_build_verifier_refuses_weak_iterations(self) -> None:
        with pytest.raises(ValueError):
            build_verifier("pencil", iterations=1024)

    def test_repository_upsert_is_idempotent_and_uses_salted_material(self, repository) -> None:
        repository.upsert("alice", build_verifier("pencil"))
        first = repository.get("alice")
        repository.upsert("alice", build_verifier("pencil"))
        second = repository.get("alice")
        assert first is not None and second is not None
        # Random salts make the two stored keys differ; neither is the password.
        assert first.salt != second.salt
        assert first.stored_key != second.stored_key
        assert repository.list_usernames() == ["alice"]

    def test_get_unknown_user_returns_none(self, repository) -> None:
        assert repository.get("nobody") is None


class TestAntiEnumeration:
    def test_unknown_users_receive_a_stable_dummy_challenge(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        challenges = []
        for idx, name in enumerate(("ghost-a", "ghost-b")):
            client = ScramClientStateMachine(name, "whatever", client_nonce=f"enum-nonce-{idx}-0123456789ab")
            ch = server.receive_client_first(client.client_first_message(), request_id=f"req-enum-{idx}")
            challenges.append(ch.message)
        # Salt and iteration count must be identical across unknown users;
        # only the nonce (which contains the client fragment) differs.
        def salt_of(msg: str) -> str:
            return msg.split(",s=", 1)[1].split(",", 1)[0]

        def iters_of(msg: str) -> str:
            return msg.rsplit(",i=", 1)[1]

        assert salt_of(challenges[0]) == salt_of(challenges[1])
        assert iters_of(challenges[0]) == iters_of(challenges[1]) == "4096"

    def test_dummy_verifier_is_reused_not_rederived(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        assert server._cached_dummy is None  # type: ignore[attr-defined]
        client = ScramClientStateMachine("ghost", "x")
        server.receive_client_first(client.client_first_message(), request_id="req-dummy-1")
        first = server._cached_dummy  # type: ignore[attr-defined]
        assert first is not None
        client2 = ScramClientStateMachine("ghost2", "x")
        server.receive_client_first(client2.client_first_message(), request_id="req-dummy-2")
        assert server._cached_dummy is first  # type: ignore[attr-defined]
