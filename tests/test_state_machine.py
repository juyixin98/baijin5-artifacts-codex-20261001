"""End-to-end state-machine acceptance tests.

Covers the four mandated scenarios plus mutual authentication:

* RFC 7677 fixed vector reproduces byte-for-byte on both sides;
* tampered final client proof -> ``invalid-proof`` and terminal session;
* nonce replay -> ``nonce-replay``;
* concurrent independent sessions stay isolated;
* server signature (``v=``) tamper -> ``server-signature-invalid`` client side;
* failed sessions cannot reuse intermediate state.
"""
from __future__ import annotations

import base64
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from scram_auth.client import ScramClientStateMachine
from scram_auth.errors import (
    FailureCategory,
    InvalidProof,
    NonceMismatch,
    NonceReplay,
    ProtocolViolation,
    ScramError,
    ServerSignatureInvalid,
    SessionReuse,
    WeakParameters,
)
from scram_auth.server import ServerPhase, ScramServerStateMachine
from scram_auth.verifiers import VerifierRepository, build_verifier

from ._oracle_stdlib import derive_all, tamper


def _seed_rfc_account(repo: VerifierRepository, vector: dict) -> None:
    salt = base64.b64decode(vector["salt_b64"])
    verifier = build_verifier(
        vector["password"],
        salt=salt,
        iterations=vector["iteration_count"],
    )
    repo.upsert(vector["username"], verifier)


def _fixed_nonce_server(config, repo, audit, vector: dict) -> ScramServerStateMachine:
    fragment = vector["server_nonce_fragment"]
    return ScramServerStateMachine(
        config, repo, audit, nonce_factory=lambda _length: fragment
    )


class TestRfc7677FixedVector:
    def test_full_exchange_matches_rfc_bytes(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        server = _fixed_nonce_server(config, repository, audit, rfc_vector)

        client = ScramClientStateMachine(
            rfc_vector["username"],
            rfc_vector["password"],
            client_nonce=rfc_vector["client_nonce"],
        )
        first = client.client_first_message()
        assert first == rfc_vector["client_first_full"]

        challenge = server.receive_client_first(first, request_id="req-rfc-1")
        assert challenge.message == rfc_vector["server_first"]

        final = client.handle_server_first(challenge.message)
        assert final == rfc_vector["client_final"]

        result = server.receive_client_final(
            final, session_id=challenge.session_id, request_id="req-rfc-1"
        )
        assert result.message == rfc_vector["server_final"]

        client.handle_server_final(result.message)
        assert client.phase.value == "verified"

    def test_server_verifier_keys_match_independent_oracle(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        stored = repository.get("user")
        assert stored is not None
        oracle = derive_all(
            "pencil",
            base64.b64decode(rfc_vector["salt_b64"]),
            4096,
            rfc_vector["client_first_bare"],
            rfc_vector["server_first"],
            rfc_vector["client_final_without_proof"],
        )
        assert stored.stored_key == oracle.stored_key
        assert stored.server_key == oracle.server_key
        # The repository must hold neither SaltedPassword nor ClientKey.
        assert stored.stored_key != oracle.client_key


class TestProofVerification:
    def test_tampered_client_proof_is_invalid_proof(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        server = _fixed_nonce_server(config, repository, audit, rfc_vector)
        client = ScramClientStateMachine("user", "pencil", client_nonce=rfc_vector["client_nonce"])
        challenge = server.receive_client_first(client.client_first_message(), request_id="req-tamper-1")
        final = client.handle_server_first(challenge.message)

        head, _, proof_field = final.partition(",p=")
        forged = f"{head},p={tamper(proof_field)}"
        with pytest.raises(InvalidProof) as exc:
            server.receive_client_final(forged, session_id=challenge.session_id, request_id="req-tamper-1")
        assert exc.value.category == FailureCategory.INVALID_PROOF
        assert server.is_terminal(challenge.session_id) is True

    def test_wrong_password_fails_as_invalid_proof(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "wrong-password")
        challenge = server.receive_client_first(client.client_first_message(), request_id="req-pw-1")
        bad_final = client.handle_server_first(challenge.message)
        with pytest.raises(InvalidProof):
            server.receive_client_final(bad_final, session_id=challenge.session_id, request_id="req-pw-1")

    def test_handle_server_first_is_one_shot(self) -> None:
        client = ScramClientStateMachine("user", "pencil", client_nonce="one-shot-nonce-0123456789")
        client.client_first_message()
        message = "r=one-shot-nonce-0123456789SERVERFRAG012345,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"
        client.handle_server_first(message)
        with pytest.raises(SessionReuse):
            client.handle_server_first(message)

    def test_unknown_user_is_reported_as_invalid_proof_not_enumeration(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("ghost", "whatever")
        challenge = server.receive_client_first(client.client_first_message(), request_id="req-ghost-1")
        final = client.handle_server_first(challenge.message)
        with pytest.raises(InvalidProof):
            server.receive_client_final(final, session_id=challenge.session_id, request_id="req-ghost-1")

    def test_tampered_server_signature_fails_client_side(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "pencil")
        challenge = server.receive_client_first(client.client_first_message(), request_id="req-v-1")
        final = client.handle_server_first(challenge.message)
        result = server.receive_client_final(final, session_id=challenge.session_id, request_id="req-v-1")

        forged_server_final = "v=" + tamper(result.message[2:])
        with pytest.raises(ServerSignatureInvalid) as exc:
            client.handle_server_final(forged_server_final)
        assert exc.value.category == FailureCategory.SERVER_SIGNATURE_INVALID
        assert client.phase.value == "failed"

    def test_client_accepts_only_one_server_final(self, config, repository, audit) -> None:
        repository.upsert("user", build_verifier("pencil"))
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "pencil")
        ch = server.receive_client_first(client.client_first_message(), request_id="req-once-1")
        final = client.handle_server_first(ch.message)
        result = server.receive_client_final(final, session_id=ch.session_id, request_id="req-once-1")
        client.handle_server_final(result.message)
        with pytest.raises(SessionReuse) as exc:
            client.handle_server_final(result.message)
        assert exc.value.category == FailureCategory.SESSION_REUSE


class TestFailedSessionCannotReuseState:
    def test_failed_session_rejects_further_finals(self, config, repository, audit, rfc_vector) -> None:
        _seed_rfc_account(repository, rfc_vector)
        server = _fixed_nonce_server(config, repository, audit, rfc_vector)

        good_client = ScramClientStateMachine("user", "pencil", client_nonce=rfc_vector["client_nonce"])
        challenge = server.receive_client_first(good_client.client_first_message(), request_id="req-reuse-1")
        good_final = good_client.handle_server_first(challenge.message)

        # 1. Tamper once -> FAILED.
        head, _, proof = good_final.partition(",p=")
        with pytest.raises(InvalidProof):
            server.receive_client_final(
                f"{head},p={tamper(proof)}", session_id=challenge.session_id, request_id="req-reuse-1"
            )
        # 2. Replaying even the *correct* final must not resurrect the session.
        with pytest.raises(SessionReuse) as exc:
            server.receive_client_final(good_final, session_id=challenge.session_id, request_id="req-reuse-1")
        assert exc.value.category == FailureCategory.SESSION_REUSE
        assert exc.value.detail["phase"] == ServerPhase.FAILED.value

    def test_successful_session_cannot_be_replayed(self, config, repository, audit) -> None:
        verifier = build_verifier("pencil")
        repository.upsert("user", verifier)
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "pencil")
        ch = server.receive_client_first(client.client_first_message(), request_id="req-reuse-ok")
        final = client.handle_server_first(ch.message)
        result = server.receive_client_final(final, session_id=ch.session_id, request_id="req-reuse-ok")
        client.handle_server_final(result.message)
        with pytest.raises(SessionReuse):
            server.receive_client_final(final, session_id=ch.session_id, request_id="req-reuse-ok")


class TestNonceRules:
    def test_repeated_client_nonce_is_replay(self, config, repository, audit) -> None:
        verifier = build_verifier("pencil")
        repository.upsert("user", verifier)
        server = ScramServerStateMachine(config, repository, audit)
        c1 = ScramClientStateMachine("user", "pencil", client_nonce="fixed-nonce-0123456789abcd")
        c2 = ScramClientStateMachine("user", "pencil", client_nonce="fixed-nonce-0123456789abcd")
        server.receive_client_first(c1.client_first_message(), request_id="req-replay-1")
        with pytest.raises(NonceReplay) as exc:
            server.receive_client_first(c2.client_first_message(), request_id="req-replay-2")
        assert exc.value.category == FailureCategory.NONCE_REPLAY

    def test_server_nonce_must_extend_client_nonce(self, rfc_vector) -> None:
        client = ScramClientStateMachine("user", "pencil", client_nonce=rfc_vector["client_nonce"])
        client.client_first_message()
        # Server echoes the client nonce verbatim (empty fragment).
        with pytest.raises(NonceMismatch) as exc:
            client.handle_server_first(
                f"r={rfc_vector['client_nonce']},s={rfc_vector['salt_b64']},i=4096"
            )
        assert exc.value.category == FailureCategory.NONCE_MISMATCH
        # Server returns an unrelated nonce.
        client2 = ScramClientStateMachine("user", "pencil", client_nonce=rfc_vector["client_nonce"])
        client2.client_first_message()
        with pytest.raises(NonceMismatch):
            client2.handle_server_first("r=totally-different-nonce-0123456789,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")

    def test_final_nonce_cannot_be_swapped(self, config, repository, audit) -> None:
        verifier = build_verifier("pencil")
        repository.upsert("user", verifier)
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "pencil")
        ch = server.receive_client_first(client.client_first_message(), request_id="req-swap-1")
        final = client.handle_server_first(ch.message)
        # Rewrite the r= attribute in BOTH halves of the final message consistently.
        swapped = final.replace(client.client_nonce, "x" * len(client.client_nonce), 1)
        swapped = swapped.replace(client.client_nonce, "x" * len(client.client_nonce), 1)
        with pytest.raises(ScramError) as exc:
            server.receive_client_final(swapped, session_id=ch.session_id, request_id="req-swap-1")
        assert exc.value.category == FailureCategory.NONCE_MISMATCH

    def test_short_client_nonce_rejected(self, config, repository, audit) -> None:
        server = ScramServerStateMachine(config, repository, audit)
        client = ScramClientStateMachine("user", "pencil", client_nonce="short")
        with pytest.raises(ProtocolViolation):
            server.receive_client_first(client.client_first_message(), request_id="req-short-1")

    def test_client_rejects_low_iteration_count(self) -> None:
        client = ScramClientStateMachine("user", "pencil")
        client.client_first_message()
        nonce = client.client_nonce + "SERVERFRAG0123456789"
        with pytest.raises(WeakParameters) as exc:
            client.handle_server_first(f"r={nonce},s=W22ZaJ0SNY7soEsUEjb6gQ==,i=1024")
        assert exc.value.detail["floor"] == 4096


class TestConcurrentSessions:
    def test_many_parallel_exchanges_stay_isolated(self, config, repository, audit) -> None:
        for i in range(12):
            repository.upsert(f"user{i:02d}", build_verifier(f"password-{i:02d}"))
        server = ScramServerStateMachine(config, repository, audit)

        def run(idx: int) -> tuple[int, str, bool]:
            req = f"req-concurrent-{idx:02d}"
            client = ScramClientStateMachine(f"user{idx:02d}", f"password-{idx:02d}")
            ch = server.receive_client_first(client.client_first_message(), request_id=req)
            final = client.handle_server_first(ch.message)
            result = server.receive_client_final(final, session_id=ch.session_id, request_id=req)
            client.handle_server_final(result.message)
            return idx, result.username, client.phase.value == "verified"

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = [future.result() for future in [pool.submit(run, i) for i in range(12)]]
        assert len(outcomes) == 12
        assert all(verified for _, _, verified in outcomes)
        assert {idx for idx, _, _ in outcomes} == set(range(12))
        assert server.active_session_count() == 12

    def test_submitting_final_to_wrong_session_fails(self, config, repository, audit) -> None:
        repository.upsert("a", build_verifier("pa"))
        repository.upsert("b", build_verifier("pb"))
        server = ScramServerStateMachine(config, repository, audit)
        ca = ScramClientStateMachine("a", "pa")
        cb = ScramClientStateMachine("b", "pb")
        cha = server.receive_client_first(ca.client_first_message(), request_id="req-cross-a")
        chb = server.receive_client_first(cb.client_first_message(), request_id="req-cross-b")
        final_a = ca.handle_server_first(cha.message)
        cb.handle_server_first(chb.message)
        # A's final, presented against B's session id, must not authenticate.
        with pytest.raises(ScramError) as exc:
            server.receive_client_final(final_a, session_id=chb.session_id, request_id="req-cross-b")
        assert exc.value.category in (
            FailureCategory.NONCE_MISMATCH,
            FailureCategory.CHANNEL_BINDING_MISMATCH,
            FailureCategory.INVALID_PROOF,
        )
        # B's session is now terminal-failed and cannot be revived.
        with pytest.raises(SessionReuse):
            server.receive_client_final(final_a, session_id=chb.session_id, request_id="req-cross-b")


class TestStoredMaterial:
    def test_database_contains_no_plaintext_password(self, config, repository) -> None:
        repository.upsert("user", build_verifier("pencil"))
        with open(config.server.database_path, "rb") as fh:
            db_bytes = fh.read()
        assert b"pencil" not in db_bytes
        # Direct schema inspection: only expected columns exist.
        conn = sqlite3.connect(config.server.database_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(scram_verifiers)")}
        conn.close()
        assert cols == {"username", "salt", "iteration_count", "stored_key", "server_key", "created_at"}
