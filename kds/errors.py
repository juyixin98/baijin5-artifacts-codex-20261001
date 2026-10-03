"""Error taxonomy for the derivation service.

Every failure raised by the service carries a stable ``category`` string so
that callers, the HTTP layer and the audit log can distinguish *why* an
operation failed without parsing message text:

- ``input``              – the request itself is invalid (bad label charset,
                           non-positive length, malformed encoding, ...).
- ``state_conflict``     – the request clashes with persisted state
                           (e.g. re-registering a key id with a different
                           display name).
- ``resource_exhausted`` – a bound was exceeded: the HKDF output-length
                           ceiling or a configured per-tenant quota.
- ``computation``        – the cryptographic backend failed unexpectedly.
"""

from __future__ import annotations

CATEGORY_INPUT = "input"
CATEGORY_STATE_CONFLICT = "state_conflict"
CATEGORY_RESOURCE_EXHAUSTED = "resource_exhausted"
CATEGORY_COMPUTATION = "computation"


class KdsError(Exception):
    """Base class for all service errors; ``category`` is machine-readable."""

    category = "internal"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict:
        return {
            "error": self.category,
            "message": self.message,
            "detail": self.detail,
        }


class InputError(KdsError):
    category = CATEGORY_INPUT


class StateConflictError(KdsError):
    category = CATEGORY_STATE_CONFLICT


class ResourceExhaustedError(KdsError):
    category = CATEGORY_RESOURCE_EXHAUSTED


class ComputationError(KdsError):
    category = CATEGORY_COMPUTATION
