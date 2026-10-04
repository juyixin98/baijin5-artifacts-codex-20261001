"""Channel-binding scope and negotiation tests.

Supported scope: no binding (GS2 ``n``/``y``) under mode ``none``, and
``p=tls-server-end-point`` under mode ``tls-server-end-point``.  ``tls-unique``
and ``tls-exporter`` are rejected at the parser and never reach the state
machine.
"""
from __future__ import annotations

import pytest

from scram_auth.client import ScramClientStateMachine
from scram_auth.errors import (
    ChannelBindingMismatch,
    FailureCategory,
    UnsupportedChannelBinding,
)
from scram_auth.verifiers import build_verifier

CERT_HASH_A = bytes.fromhex("aa" * 32)
CERT_HASH_B = bytes.fromhex("bb" * 32)


def _run_exchange(server, username: str, password: str, **client_kw) -> None:
    client = ScramClientStateMachine(username, password, **client_kw)
    ch = server.receive_client_first(
        client.client_first_message(),
        request_id="req-cb",
        tls_endpoint_hash=client_kw.get("tls_endpoint_hash"),
    )
    final = client.handle_server_first(ch.message)
    result = server.receive_client_final(final, session_id=ch.session_id, request_id="req-cb")
    client.handle_server_final(result.message)
    assert client.phase.value == "verified"


class TestNonPlusMode:
    def test_flag_n_authenticates(self, config, repository, audit) -> None:
        repository.upsert("alice", build_verifier("pencil"))
        server = __import__("scram_auth.server", fromlist=["ScramServerStateMachine"]).ScramServerStateMachine(
            config, repository, audit
        )
        _run_exchange(server, "alice", "pencil", channel_binding="n")

    def test_flag_y_is_accepted_when_client_does_not_support_binding(self, config, repository, audit) -> None:
        from scram_auth.server import ScramServerStateMachine

        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("alice", "pencil", channel_binding="y")
        ch = server.receive_client_first(client.client_first_message(), request_id="req-cb-y")
        final = client.handle_server_first(ch.message)
        result = server.receive_client_final(final, session_id=ch.session_id, request_id="req-cb-y")
        client.handle_server_final(result.message)
        assert client.phase.value == "verified"

    def test_plus_offer_rejected_when_server_has_no_binding(self, config, repository, audit) -> None:
        from scram_auth.server import ScramServerStateMachine

        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine(
            "alice", "pencil", channel_binding="tls-server-end-point", tls_endpoint_hash=CERT_HASH_A
        )
        with pytest.raises(UnsupportedChannelBinding) as exc:
            server.receive_client_first(
                client.client_first_message(), request_id="req-cb-plus-off", tls_endpoint_hash=CERT_HASH_A
            )
        assert exc.value.category == FailureCategory.UNSUPPORTED_CHANNEL_BINDING


class TestPlusModeTlsServerEndPoint:
    def test_plus_exchange_authenticates_with_matching_hash(self, plus_config, repository, audit) -> None:
        from scram_auth.server import ScramServerStateMachine

        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(plus_config, repository, audit)
        _run_exchange(
            server,
            "alice",
            "pencil",
            channel_binding="tls-server-end-point",
            tls_endpoint_hash=CERT_HASH_A,
        )

    def test_downgrade_to_non_plus_is_rejected(self, plus_config, repository, audit) -> None:
        from scram_auth.server import ScramServerStateMachine

        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(plus_config, repository, audit)
        client = ScramClientStateMachine("alice", "pencil", channel_binding="n")
        with pytest.raises(ChannelBindingMismatch) as exc:
            server.receive_client_first(client.client_first_message(), request_id="req-cb-downgrade")
        assert exc.value.category == FailureCategory.CHANNEL_BINDING_MISMATCH
        assert exc.value.detail["server_mode"] == "tls-server-end-point"

    def test_mismatched_cert_hash_fails_final(self, plus_config, repository, audit) -> None:
        from scram_auth.server import ScramServerStateMachine

        repository.upsert("alice", build_verifier("pencil"))
        server = ScramServerStateMachine(plus_config, repository, audit)
        # Client binds to cert A, but the transport actually presented cert B.
        client = ScramClientStateMachine(
            "alice", "pencil", channel_binding="tls-server-end-point", tls_endpoint_hash=CERT_HASH_A
        )
        ch = server.receive_client_first(
            client.client_first_message(), request_id="req-cb-mismatch", tls_endpoint_hash=CERT_HASH_B
        )
        final = client.handle_server_first(ch.message)
        with pytest.raises(ChannelBindingMismatch):
            server.receive_client_final(final, session_id=ch.session_id, request_id="req-cb-mismatch")

    def test_client_refuses_plus_without_hash(self) -> None:
        with pytest.raises(UnsupportedChannelBinding):
            ScramClientStateMachine("alice", "pencil", channel_binding="tls-server-end-point")

    def test_client_refuses_hash_for_y_flag(self) -> None:
        with pytest.raises(Exception):
            ScramClientStateMachine(
                "alice", "pencil", channel_binding="y", tls_endpoint_hash=CERT_HASH_A
            )
