"""Error taxonomy for the FIR estimation backend.

Every failure raised by the backend carries a category so that callers
(API layer, tests, operators reading run logs) can distinguish:

- INPUT:      the caller supplied invalid samples or parameters (HTTP 400)
- STATE:      the request conflicts with the stream/session state (HTTP 409)
- RESOURCE:   the request exceeds configured size limits (HTTP 413)
- COMPUTATION: the numerical pipeline itself failed (HTTP 500)
"""

from __future__ import annotations

import enum
from typing import Any


class ErrorCategory(str, enum.Enum):
    INPUT = "input_error"
    STATE = "state_conflict"
    RESOURCE = "resource_exhausted"
    COMPUTATION = "computation_failure"


class FirBackendError(Exception):
    """Base class for all backend errors. Carries a stable category."""

    category: ErrorCategory = ErrorCategory.COMPUTATION

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }


class InputValidationError(FirBackendError):
    """Samples or parameters violate the input contract."""

    category = ErrorCategory.INPUT


class StateConflictError(FirBackendError):
    """Operation not allowed in the current stream/session state."""

    category = ErrorCategory.STATE


class ResourceExhaustedError(FirBackendError):
    """Configured resource limits (samples, order, sessions) exceeded."""

    category = ErrorCategory.RESOURCE


class ComputationError(FirBackendError):
    """The numerical pipeline failed (non-finite result, solver failure)."""

    category = ErrorCategory.COMPUTATION
