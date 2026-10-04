"""Client-side SCRAM-SHA-256 state machine (RFC 5802 with RFC 7677).

Lifecycle::

    NEED_SERVER_FIRST --(server-first)--> NEED_SERVER_FINAL
        --(server-final v verified)--> VERIFIED (terminal, success)
    any error -> FAILED (terminal); the object may not be re-used.

The client independently enforces:

* nonce concatenation — the server nonce must strictly extend the client
  nonce, and the final-round nonce must equal it byte-for-byte;
* iteration-count floor (RFC 7677: at least 4096);
* channel-binding negotiation (``n``/``y`` vs ``p=tls-server-end-point``);
* the server signature (mutual authentication), failing with the explicit
  :class:`ServerSignatureInvalid` category.
"""
from __future__ import annotations

import enum
import hmac

from . import crypto, wire
from .errors import (
    NonceMismatch,
    ProtocolViolation,
    ScramError,
    ServerSignatureInvalid,
    SessionReuse,
    UnsupportedChannelBinding,
    WeakParameters,
)
from .saslprep import sasl_prep

RFC7677_MIN_ITERATIONS = 4096


class ClientPhase(str, enum.Enum):
    NEED_SERVER_FIRST = "need-server-first"
    NEED_SERVER_FINAL = "need-server-final"
    VERIFIED = "verified"
    FAILED = "failed"


class ScramClientStateMachine:
    """One-shot client exchange; construct a new instance per authentication."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        channel_binding: str = "n",
        tls_endpoint_hash: bytes | None = None,
        client_nonce: str | None = None,
        min_iterations: int = RFC7677_MIN_ITERATIONS,
    ) -> None:
        if channel_binding not in ("n", "y", wire.CB_TLS_SERVER_END_POINT):
            raise UnsupportedChannelBinding(
                f"client channel_binding must be n/y/{wire.CB_TLS_SERVER_END_POINT}",
                detail={"observed": channel_binding},
            )
        if channel_binding == "y" and tls_endpoint_hash is not None:
            raise ProtocolViolation("GS2 flag y means the client does NOT support PLUS; hash must not be supplied")
        if channel_binding == wire.CB_TLS_SERVER_END_POINT and not tls_endpoint_hash:
            raise UnsupportedChannelBinding(
                "PLUS selected but no tls-server-end-point certificate hash was provided"
            )
        self._phase = ClientPhase.NEED_SERVER_FIRST
        self._username = sasl_prep(username)
        self._password_bytes = sasl_prep(password).encode("utf-8")
        self._cb_flag = "p" if channel_binding == wire.CB_TLS_SERVER_END_POINT else channel_binding
        self._cb_type = wire.CB_TLS_SERVER_END_POINT if self._cb_flag == "p" else None
        self._tls_hash = tls_endpoint_hash
        self._client_nonce = client_nonce or crypto.random_nonce(24)
        self._min_iterations = min_iterations
        self._gs2 = wire.Gs2Header(cb_flag=self._cb_flag, cb_type=self._cb_type, authzid=None)
        self._server_first: wire.ServerFirst | None = None
        self._auth_message: bytes | None = None
        self._client_final_without_proof: str | None = None
        self._stored_server_key: bytes | None = None
        self._first_emitted = False

    @property
    def phase(self) -> ClientPhase:
        return self._phase

    @property
    def client_nonce(self) -> str:
        return self._client_nonce

    def _fail(self, err: ScramError) -> None:
        self._phase = ClientPhase.FAILED
        self._password_bytes = b""
        self._auth_message = None
        raise err

    def _ensure_active(self) -> None:
        if self._phase in (ClientPhase.VERIFIED, ClientPhase.FAILED):
            raise SessionReuse(
                f"client exchange is terminal ({self._phase.value}); create a new instance per authentication",
                detail={"phase": self._phase.value},
            )

    # ------------------------------------------------------------- step 1
    def client_first_message(self) -> str:
        """Build ``n,,n=<user>,r=<cnonce>`` (or the ``p=`` GS2 variant)."""
        self._ensure_active()
        if self._first_emitted:
            raise SessionReuse("client-first message can only be produced once")
        self._first_emitted = True
        bare = f"n={wire.escape_username(self._username)},r={self._client_nonce}"
        return self._gs2.serialise().decode("utf-8") + bare

    # ------------------------------------------------------------- step 2
    def handle_server_first(self, message: str) -> str:
        """Validate the server challenge and return the client-final message."""
        self._ensure_active()
        if self._phase != ClientPhase.NEED_SERVER_FIRST:
            raise SessionReuse("a server-first message was already processed")
        try:
            parsed = wire.parse_server_first(message, max_attributes=8)
            # Nonce concatenation check: server nonce MUST start with the
            # client nonce and MUST be strictly longer (RFC 5802 2.3 / 5.1).
            if not parsed.nonce.startswith(self._client_nonce):
                raise NonceMismatch(
                    "server nonce does not begin with the client nonce",
                    detail={"client_len": len(self._client_nonce), "server_len": len(parsed.nonce)},
                )
            if len(parsed.nonce) <= len(self._client_nonce):
                raise NonceMismatch("server appended an empty nonce fragment")
            if parsed.iteration_count < self._min_iterations:
                raise WeakParameters(
                    f"server iteration count {parsed.iteration_count} is below the client floor "
                    f"{self._min_iterations}",
                    detail={"observed": parsed.iteration_count, "floor": self._min_iterations},
                )

            salted = crypto.derive_salted_password(self._password_bytes, parsed.salt, parsed.iteration_count)
            ck = crypto.client_key(salted)
            stored = crypto.stored_key_from_client_key(ck)
            srv_key = crypto.server_key(salted)

            cb_value = wire.channel_binding_value(self._gs2, self._tls_hash)
            without_proof = wire.build_client_final_without_proof(cb_value, parsed.nonce)
            client_first_bare = f"n={wire.escape_username(self._username)},r={self._client_nonce}"
            auth_message = f"{client_first_bare},{message},{without_proof}".encode("utf-8")
            client_sig = crypto.client_signature(stored, auth_message)
            proof = crypto.make_client_proof(ck, client_sig)
            final = wire.build_client_final(without_proof, proof)

            self._phase = ClientPhase.NEED_SERVER_FINAL
            self._server_first = parsed
            self._auth_message = auth_message
            self._client_final_without_proof = without_proof
            self._stored_server_key = srv_key
            return final
        except ScramError as err:
            self._fail(err)

    # ------------------------------------------------------------- step 4
    def handle_server_final(self, message: str) -> None:
        """Verify the server ``v=`` signature; raise on any mismatch."""
        self._ensure_active()
        if self._phase != ClientPhase.NEED_SERVER_FINAL:
            raise SessionReuse("server-final can only be verified once, in the correct phase")
        try:
            verifier = wire.parse_server_final_verifier(message, max_attributes=8)
            if self._auth_message is None or self._stored_server_key is None:
                # Defensive: the phase machine guarantees these are set here;
                # fail closed rather than dereference None.
                self._fail(
                    ServerSignatureInvalid(
                        "internal client state is incomplete before server-final verification"
                    )
                )
            expected = crypto.server_signature(self._stored_server_key, self._auth_message)
            if len(verifier) != crypto.HASH_DIGEST_SIZE or not hmac.compare_digest(verifier, expected):
                raise ServerSignatureInvalid(
                    "server signature v= does not match HMAC(ServerKey, AuthMessage); "
                    "server is not authenticated or the exchange was tampered with"
                )
            self._phase = ClientPhase.VERIFIED
            self._password_bytes = b""
        except ScramError as err:
            self._fail(err)
