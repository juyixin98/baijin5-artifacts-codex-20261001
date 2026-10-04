"""Server-side SCRAM-SHA-256 state machine (RFC 5802 with RFC 7677).

Lifecycle of one server session::

    RECEIVED_CLIENT_FIRST --(client-final ok)--> VERIFIED (terminal, success)
            |
            `------(any error)-----------------> FAILED  (terminal)

Terminal sessions are never re-entered: every subsequent call for their id
raises :class:`SessionReuse` (unknown/expired ids raise
:class:`SessionNotFound`/``SessionExpired``).  All sensitive intermediate
material lives only inside the session object and is dropped on terminal
transition.

Replay defence: the SHA-256 of every accepted client nonce is remembered for
the session TTL window; a second exchange presenting the same nonce fails as
:class:`NonceReplay` before any crypto work.
"""
from __future__ import annotations

import enum
import hashlib
import hmac
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from . import crypto, wire
from .audit import AuditLogger
from .config import AppConfig
from .errors import (
    ChannelBindingMismatch,
    NonceMismatch,
    NonceReplay,
    ProtocolViolation,
    ScramError,
    SessionExpired,
    SessionNotFound,
    SessionReuse,
    UnsupportedChannelBinding,
)
from .verifiers import Verifier, VerifierRepository

LOCATION = "src/scram_auth/server.py"


class ServerPhase(str, enum.Enum):
    AWAIT_CLIENT_FINAL = "await-client-final"
    VERIFIED = "verified"
    FAILED = "failed"


@dataclass
class _ServerSession:
    session_id: str
    request_id: str
    phase: ServerPhase
    created_at: float
    username: str
    client_nonce: str
    full_nonce: str
    gs2: wire.Gs2Header
    client_first_bare: str
    server_first: str
    auth_message: str | None
    verifier: Verifier | None
    account_exists: bool = False
    cb_endpoint_hash: bytes | None = None


@dataclass(frozen=True)
class ServerFirstChallenge:
    session_id: str
    message: str


@dataclass(frozen=True)
class ServerFinalResult:
    session_id: str
    username: str
    message: str  # v=...


@dataclass
class _NonceEntry:
    expires_at: float


class ScramServerStateMachine:
    """Stateful server; one instance per service, safe for concurrent calls."""

    def __init__(
        self,
        config: AppConfig,
        repository: VerifierRepository,
        audit: AuditLogger,
        *,
        nonce_factory: Callable[[int], str] | None = None,
    ) -> None:
        self._config = config
        self._repo = repository
        self._audit = audit
        # Injectable for deterministic RFC-vector reproduction; production uses
        # the CSPRNG-backed ``crypto.random_nonce``.
        self._nonce_factory = nonce_factory or crypto.random_nonce
        self._sessions: dict[str, _ServerSession] = {}
        # key = sha256 hex of client nonce -> expiry record
        self._seen_nonces: dict[str, _NonceEntry] = {}
        # Lazily derived, once per process, anti-enumeration dummy verifier.
        self._cached_dummy: Verifier | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ utils
    def _fail_locked(self, session: _ServerSession | None, err: ScramError) -> None:
        if session is not None:
            session.phase = ServerPhase.FAILED
            session.auth_message = None
            session.verifier = None
            session.gs2 = None  # type: ignore[assignment]
        if session is not None:
            self._audit.failure(
                session.request_id,
                event="auth_failure",
                step=type(err).__name__,
                session_id=session.session_id,
                failure=err.to_dict(),
                location=LOCATION,
            )

    def _purge_expired_locked(self, now: float) -> None:
        ttl = self._config.session.ttl_seconds
        expired_sessions = [sid for sid, s in self._sessions.items() if now - s.created_at > ttl]
        for sid in expired_sessions:
            del self._sessions[sid]
        dead_nonces = [n for n, e in self._seen_nonces.items() if now > e.expires_at]
        for n in dead_nonces:
            del self._seen_nonces[n]

    def _get_live_session_locked(self, session_id: str) -> _ServerSession:
        now = time.time()
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFound(f"unknown or already-reaped session {session_id[:8]}…")
        if now - session.created_at > self._config.session.ttl_seconds:
            del self._sessions[session_id]
            raise SessionExpired(f"session {session_id[:8]}… exceeded TTL of {self._config.session.ttl_seconds}s")
        if session.phase in (ServerPhase.VERIFIED, ServerPhase.FAILED):
            raise SessionReuse(
                f"session {session_id[:8]}… is terminal ({session.phase.value}); intermediate state cannot be reused",
                detail={"phase": session.phase.value},
            )
        return session

    # ------------------------------------------------------- channel binding
    def _enforce_channel_binding_policy_locked(self, gs2: wire.Gs2Header, cert_hash: bytes | None) -> None:
        mode = self._config.channel_binding.mode
        if gs2.cb_flag == "p":
            if mode != "tls-server-end-point":
                # Parser already limits the type; defence in depth.
                raise UnsupportedChannelBinding(
                    "PLUS requested but this server is configured without channel-binding support",
                    detail={"server_mode": mode},
                )
            if gs2.cb_type != wire.CB_TLS_SERVER_END_POINT:
                raise UnsupportedChannelBinding(
                    f"channel-binding type {gs2.cb_type!r} not in supported scope",
                    detail={"scope": [wire.CB_TLS_SERVER_END_POINT]},
                )
            if not cert_hash:
                raise ChannelBindingMismatch(
                    "p=tls-server-end-point selected but no server certificate hash reached the state machine"
                )
            return
        # gs2 flag n or y
        if mode == "tls-server-end-point":
            # Strict local policy: the transport is TLS with a known end-point
            # hash, so downgrading to non-PLUS is rejected rather than silently
            # authenticating without binding. (RFC 5802 section 6 guidance.)
            raise ChannelBindingMismatch(
                f"server requires PLUS ({mode}) but client offered GS2 flag {gs2.cb_flag!r}",
                detail={"server_mode": mode, "client_flag": gs2.cb_flag},
            )

    # ------------------------------------------------------------- step 1/2
    def receive_client_first(
        self,
        message: str,
        *,
        request_id: str,
        tls_endpoint_hash: bytes | None = None,
    ) -> ServerFirstChallenge:
        """Process ``client-first-message`` and return the server challenge."""
        self._validate_envelope_size(message)
        with self._lock:
            now = time.time()
            self._purge_expired_locked(now)
            if len(self._sessions) >= self._config.session.max_active:
                # Reaping first gives expired sessions a chance to free slots.
                raise ProtocolViolation("too many active sessions; retry later", detail={"cap": self._config.session.max_active})
            try:
                parsed = wire.parse_client_first(message, max_attributes=self._config.limits.max_attributes)
                if len(parsed.username) > self._config.limits.max_username_chars:
                    raise ProtocolViolation("username exceeds configured length limit")
                self._check_nonce_shape(parsed.client_nonce)
                self._enforce_channel_binding_policy_locked(parsed.gs2, tls_endpoint_hash)
                if parsed.gs2.authzid is not None:
                    # Local test scope: no authorization-identity identity switching.
                    raise ProtocolViolation(
                        "authzid (a=) is not supported in the local test service",
                        detail={"has_authzid": True},
                    )
                nonce_key = hashlib.sha256(parsed.client_nonce.encode("ascii")).hexdigest()
                if nonce_key in self._seen_nonces:
                    raise NonceReplay("client nonce was already used in a prior exchange")
                known_verifier = self._repo.get(parsed.username)
                account_exists = known_verifier is not None
                server_frag = self._nonce_factory(self._config.crypto.server_nonce_bytes)
                full_nonce = parsed.client_nonce + server_frag
                # Unknown users get a fully derived dummy verifier built ONCE
                # here so first and final stages cost the same as a known-user
                # exchange (RFC 5802 section 2.2 anti-enumeration guidance).
                verifier = known_verifier or self._dummy_verifier()
                salt, iterations = verifier.salt, verifier.iteration_count
                server_first = wire.build_server_first(full_nonce, salt, iterations)
                session = _ServerSession(
                    session_id=uuid.uuid4().hex,
                    request_id=request_id,
                    phase=ServerPhase.AWAIT_CLIENT_FINAL,
                    created_at=now,
                    username=parsed.username,
                    client_nonce=parsed.client_nonce,
                    full_nonce=full_nonce,
                    gs2=parsed.gs2,
                    client_first_bare=parsed.bare,
                    server_first=server_first,
                    auth_message=None,
                    verifier=verifier,
                    account_exists=account_exists,
                    cb_endpoint_hash=tls_endpoint_hash,
                )
                self._sessions[session.session_id] = session
                self._seen_nonces[nonce_key] = _NonceEntry(expires_at=now + self._config.session.ttl_seconds)
                self._audit.info(
                    request_id,
                    event="auth_step",
                    step="server_first_sent",
                    session_id=session.session_id,
                    location=LOCATION,
                    detail={
                        "username_seen": parsed.username,
                        "account_exists": account_exists,
                        "nonce_client_len": len(parsed.client_nonce),
                        "nonce_server_len": len(server_frag),
                        "iteration_count": iterations,
                        "gs2_flag": parsed.gs2.cb_flag,
                    },
                )
                return ServerFirstChallenge(session_id=session.session_id, message=server_first)
            except ScramError as err:
                self._audit.failure(
                    request_id,
                    event="auth_failure",
                    step="receive_client_first",
                    failure=err.to_dict(),
                    location=LOCATION,
                )
                raise

    def _check_nonce_shape(self, client_nonce: str) -> None:
        max_bytes = self._config.crypto.nonce_max_bytes
        min_bytes = self._config.crypto.client_nonce_min_bytes
        if len(client_nonce) < min_bytes:
            raise ProtocolViolation(
                f"client nonce too short: {len(client_nonce)} < {min_bytes}",
                detail={"length": len(client_nonce), "min": min_bytes},
            )
        if len(client_nonce) > max_bytes:
            raise ProtocolViolation(f"client nonce too long: {len(client_nonce)} > {max_bytes}")
        try:
            client_nonce.encode("ascii")
        except UnicodeEncodeError as exc:
            raise ProtocolViolation("client nonce must be ASCII") from exc

    def _dummy_verifier(self) -> Verifier:
        """Return the cached dummy verifier for unknown users.

        RFC 5802 section 2.2: a server MAY perform a dummy SCRAM exchange with a
        fixed iteration count/salt/StoredKey/ServerKey for unknown users so the
        visible exchange and per-stage cost is indistinguishable from a real
        account. We derive it once per process (fixed salt, fixed dummy
        password) and reuse it; neither stage then runs extra PBKDF2.
        """
        cached = getattr(self, "_cached_dummy", None)
        if cached is None:
            dummy_salt = hashlib.sha256(b"scram-auth-local fixed dummy salt v1").digest()
            salted = crypto.derive_salted_password(
                b"scram-dummy-password", dummy_salt, self._config.crypto.iteration_count
            )
            ck = crypto.client_key(salted)
            cached = Verifier(
                username="",
                salt=dummy_salt,
                iteration_count=self._config.crypto.iteration_count,
                stored_key=crypto.stored_key_from_client_key(ck),
                server_key=crypto.server_key(salted),
            )
            self._cached_dummy = cached
        return cached

    # ------------------------------------------------------------- step 3/4
    def receive_client_final(self, message: str, *, session_id: str, request_id: str) -> ServerFinalResult:
        self._validate_envelope_size(message)
        with self._lock:
            session = self._get_live_session_locked(session_id)
            try:
                parsed = wire.parse_client_final(message, max_attributes=self._config.limits.max_attributes)
                if parsed.nonce != session.full_nonce:
                    if not parsed.nonce.startswith(session.client_nonce):
                        raise NonceMismatch(
                            "final nonce does not extend the original client nonce",
                            detail={"nonce_len": len(parsed.nonce)},
                        )
                    raise NonceMismatch(
                        "final nonce differs from the server-issued concatenation",
                        detail={"nonce_len": len(parsed.nonce)},
                    )
                if len(parsed.proof) != crypto.HASH_DIGEST_SIZE:
                    raise ProtocolViolation(
                        f"client proof must be {crypto.HASH_DIGEST_SIZE} bytes, got {len(parsed.proof)}"
                    )
                expected_cb = wire.channel_binding_value(session.gs2, session.cb_endpoint_hash)
                if not hmac.compare_digest(expected_cb, parsed.channel_binding):
                    raise ChannelBindingMismatch(
                        "channel-binding header/value in client-final does not match the negotiated binding",
                        detail={"expected_len": len(expected_cb), "actual_len": len(parsed.channel_binding)},
                    )
                without_proof = wire.build_client_final_without_proof(expected_cb, session.full_nonce)
                if not hmac.compare_digest(without_proof, parsed.raw_without_proof):
                    raise ProtocolViolation(
                        "client-final-without-proof re-serialisation differs (attribute order/content changed)"
                    )

                verifier = session.verifier
                if verifier is None:
                    # Defensive: receive_client_first always stores a real or
                    # cached-dummy verifier; its absence means corrupt state.
                    raise ProtocolViolation("session verifier material is missing (corrupt state)")

                auth_message = (
                    session.client_first_bare + "," + session.server_first + "," + parsed.raw_without_proof
                ).encode("utf-8")
                client_sig = crypto.client_signature(verifier.stored_key, auth_message)
                recovered_key = crypto.recover_client_key(parsed.proof, client_sig)
                candidate_stored = crypto.stored_key_from_client_key(recovered_key)
                if not hmac.compare_digest(candidate_stored, verifier.stored_key):
                    from .errors import InvalidProof

                    raise InvalidProof("client proof verification failed (invalid credentials or tampered proof)")

                server_sig = crypto.server_signature(verifier.server_key, auth_message)
                server_final = wire.build_server_final(server_sig)

                # Terminal success transition: drop all intermediate material.
                session.phase = ServerPhase.VERIFIED
                session.auth_message = None
                session.verifier = None
                self._audit.success(
                    request_id,
                    event="auth_success",
                    step="client_proof_verified",
                    session_id=session.session_id,
                    location=LOCATION,
                    detail={"username": session.username, "proof_bytes": len(parsed.proof)},
                )
                return ServerFinalResult(session_id=session.session_id, username=session.username, message=server_final)
            except ScramError as err:
                self._fail_locked(session, err)
                raise

    # ------------------------------------------------------------ inspection
    def active_session_count(self) -> int:
        with self._lock:
            self._purge_expired_locked(time.time())
            return len(self._sessions)

    def is_terminal(self, session_id: str) -> bool | None:
        """True/False for known sessions, ``None`` once reaped."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            return session.phase in (ServerPhase.VERIFIED, ServerPhase.FAILED)

    def _validate_envelope_size(self, message: str) -> None:
        if len(message.encode("utf-8")) > self._config.limits.max_message_bytes:
            raise ProtocolViolation(
                "message exceeds configured size limit",
                detail={"bytes": len(message.encode("utf-8")), "limit": self._config.limits.max_message_bytes},
            )
