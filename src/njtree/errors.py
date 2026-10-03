"""Error taxonomy for the NJ tree backend.

Every failure raised by this package is an ``NJError`` carrying a stable
``category`` so that callers (API layer, tests, operators) can distinguish:

- ``input_error``         -- malformed / invalid user input (HTTP 422)
- ``state_conflict``      -- conflicting run state, e.g. run-id reuse (HTTP 409)
- ``resource_exhausted``  -- declared limits exceeded, e.g. too many taxa (HTTP 413)
- ``computation_failure`` -- the algorithm itself could not proceed (HTTP 500)

The categories are part of the public contract: tests assert on them and the
API maps them to distinct HTTP status codes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    INPUT_ERROR = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"


class NJError(Exception):
    """Base class for all errors raised by njtree."""

    category: ErrorCategory = ErrorCategory.INPUT_ERROR

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.run_id = run_id
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "run_id": self.run_id,
            "details": self.details,
        }


class InputValidationError(NJError):
    """User input failed a declared validation check."""

    category = ErrorCategory.INPUT_ERROR


class UnknownRunError(InputValidationError):
    """A referenced run_id does not exist. Mapped to HTTP 404 by the API."""


class StateConflictError(NJError):
    """A run identifier was reused with different input, or a terminal run
    was targeted by an incompatible operation."""

    category = ErrorCategory.STATE_CONFLICT


class ResourceExhaustedError(NJError):
    """A declared resource limit (e.g. max_taxa) was exceeded."""

    category = ErrorCategory.RESOURCE_EXHAUSTED


class ComputationError(NJError):
    """The numerical computation could not proceed under the declared mode
    (e.g. a negative branch length with mode=error)."""

    category = ErrorCategory.COMPUTATION_FAILURE
