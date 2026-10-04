"""Domain error types with stable failure categories.

Every rejection path raises one of these so callers (HTTP layer, tests, audit
log) can branch on a *machine-readable category* rather than string matching.

Categories
----------
PROTOCODING   malformed framing / fields (bad lengths, unknown version ...)
NONCE_CONFLICT same nonce slot reused with different ciphertext or tag
DUP_SEGMENT   identical segment delivered twice at an unexpected sequence slot
REORDER       segment out of declared order / sequence gap detected
TRUNCATION    stream finalised without the expected terminator or segment count
AUTH_FAILED   AEAD tag verification failed (ciphertext/AAD/key mismatch)
STATE         stream in a state that forbids the action (sealed/unknown)
INCOMPLETE    stream cannot be released yet; authenticator cannot decide
LIMIT         declared or observed size limit exceeded
STORAGE       persistence / staging filesystem failure
KEY_MATERIAL  missing or invalid key configuration
"""

from __future__ import annotations


class SSEAError(Exception):
    """Base class for all service errors."""

    category = "ssea_error"
    http_status = 400

    def __init__(self, message: str, *, request_id: str | None = None,
                 **state: object) -> None:
        super().__init__(message)
        self.message = message
        self.request_id = request_id
        # State must never contain key material or plaintext; sanitise at call
        # sites (only counters, ids, booleans are placed here).
        self.state = state

    def diagnostic(self) -> dict[str, object]:
        """Structured, redaction-safe diagnostic record."""
        return {
            "category": self.category,
            "error": self.message,
            "request_id": self.request_id,
            "state": self.state,
        }


class ProtocolCodingError(SSEAError):
    category = "protocoding"
    http_status = 422


class NonceConflictError(SSEAError):
    category = "nonce_conflict"
    http_status = 409


class DuplicateSegmentError(SSEAError):
    category = "dup_segment"
    http_status = 409


class ReorderError(SSEAError):
    category = "reorder"
    http_status = 409


class TruncationError(SSEAError):
    category = "truncation"
    http_status = 422


class AuthenticationError(SSEAError):
    category = "auth_failed"
    http_status = 400


class StateError(SSEAError):
    category = "state"
    http_status = 409


class IncompleteStreamError(SSEAError):
    category = "incomplete"
    http_status = 409


class LimitError(SSEAError):
    category = "limit"
    http_status = 413


class StorageError(SSEAError):
    category = "storage"
    http_status = 500


class KeyMaterialError(SSEAError):
    category = "key_material"
    http_status = 500
