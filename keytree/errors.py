"""Error taxonomy for the keytree service.

Every failure raised by the service carries exactly one category so that
callers, tests, and audit records can distinguish:

- input_error:         malformed or out-of-contract caller input (HTTP 400)
- state_conflict:      valid input conflicting with persisted state (HTTP 409)
- resource_exhausted:  request exceeds an algorithmic or configured limit
                       (HTTP 413)
- computation_failure: the crypto backend or storage failed unexpectedly
                       (HTTP 500)

Categories are stable strings; they are part of the module's data contract
and are asserted by the independent test-suite.
"""

from __future__ import annotations

from typing import Any


class KeyTreeError(Exception):
    """Base class for all service errors. Carries a stable category."""

    category: str = "computation_failure"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


class InputValidationError(KeyTreeError):
    """Caller input violates the documented contract."""

    category = "input_error"


class StateConflictError(KeyTreeError):
    """Input is well-formed but conflicts with persisted state."""

    category = "state_conflict"


class ResourceExhaustedError(KeyTreeError):
    """Request exceeds an algorithmic bound or configured quota."""

    category = "resource_exhausted"


class ComputationError(KeyTreeError):
    """Backend (crypto or storage) failed to complete a valid request."""

    category = "computation_failure"
