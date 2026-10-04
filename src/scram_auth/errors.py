"""Typed error hierarchy for the SCRAM-SHA-256 state machines.

Every failure maps to an explicit :class:`FailureCategory` so that callers
(and audit logs) can distinguish protocol violations, credential failures,
channel-binding mismatches and session-lifecycle problems instead of
receiving a generic boolean.
"""
from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    """Stable, machine-readable failure categories.

    The string values are part of the public API surface and appear in the
    JSON error envelope and audit log; do not rename them casually.
    """

    PROTOCOL_VIOLATION = "protocol-violation"
    INVALID_ENCODING = "invalid-encoding"
    UNSUPPORTED_MECHANISM = "unsupported-mechanism"
    UNSUPPORTED_CHANNEL_BINDING = "unsupported-channel-binding"
    CHANNEL_BINDING_MISMATCH = "channel-bindings-dont-match"
    INVALID_PROOF = "invalid-proof"
    SERVER_SIGNATURE_INVALID = "server-signature-invalid"
    SESSION_NOT_FOUND = "session-not-found"
    SESSION_EXPIRED = "session-expired"
    SESSION_REUSE = "session-reuse"
    NONCE_REPLAY = "nonce-replay"
    NONCE_MISMATCH = "nonce-mismatch"
    WEAK_PARAMETERS = "weak-parameters"
    RATE_LIMITED = "rate-limited"
    INTERNAL_ERROR = "internal-error"


class ScramError(Exception):
    """Base class for all SCRAM state-machine errors."""

    category: FailureCategory = FailureCategory.INTERNAL_ERROR

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        # ``detail`` must never contain secrets; callers pass structural data
        # such as observed attribute order or expected/actual lengths only.
        self.detail: dict = dict(detail or {})

    def to_dict(self) -> dict:
        return {"category": self.category.value, "message": self.message, "detail": self.detail}


class ProtocolViolation(ScramError):
    """Malformed message: bad attribute order, duplicates, truncated fields."""

    category = FailureCategory.PROTOCOL_VIOLATION


class InvalidEncoding(ScramError):
    """Base64 or printable-ASCII encoding failure on a wire attribute."""

    category = FailureCategory.INVALID_ENCODING


class UnsupportedMechanism(ScramError):
    category = FailureCategory.UNSUPPORTED_MECHANISM


class UnsupportedChannelBinding(ScramError):
    category = FailureCategory.UNSUPPORTED_CHANNEL_BINDING


class ChannelBindingMismatch(ScramError):
    category = FailureCategory.CHANNEL_BINDING_MISMATCH


class InvalidProof(ScramError):
    """Server could not verify the client proof (bad credentials)."""

    category = FailureCategory.INVALID_PROOF


class ServerSignatureInvalid(ScramError):
    """Client could not verify the server signature."""

    category = FailureCategory.SERVER_SIGNATURE_INVALID


class SessionNotFound(ScramError):
    category = FailureCategory.SESSION_NOT_FOUND


class SessionExpired(ScramError):
    category = FailureCategory.SESSION_EXPIRED


class SessionReuse(ScramError):
    """A terminal (succeeded/failed) or wrong-state session was used again."""

    category = FailureCategory.SESSION_REUSE


class NonceReplay(ScramError):
    category = FailureCategory.NONCE_REPLAY


class NonceMismatch(ScramError):
    """Server-first nonce does not extend the client nonce, or final nonce changed."""

    category = FailureCategory.NONCE_MISMATCH


class WeakParameters(ScramError):
    """Iteration count below policy floor or other weak negotiation."""

    category = FailureCategory.WEAK_PARAMETERS
