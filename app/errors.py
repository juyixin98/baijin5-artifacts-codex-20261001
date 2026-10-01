"""Domain error hierarchy for the online-FDR teaching service.

Every error carries a stable machine-readable ``code`` so that callers (and the
test suite) can distinguish four required failure classes:

* ``INPUT_ERROR``       -- malformed external input (bad p-value, bad params)
* ``STATE_CONFLICT``    -- request contradicts committed state (duplicate id, ...)
* ``RESOURCE_EXHAUSTED``-- the frozen horizon/budget of steps is used up
* ``COMPUTATION_FAILED``-- numerical failure inside the estimation kernel
* ``NOT_FOUND``         -- unknown run
* ``INTEGRITY_ERROR``   -- replay disagrees with committed history (tampering)
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    INPUT_ERROR = "INPUT_ERROR"
    STATE_CONFLICT = "STATE_CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    COMPUTATION_FAILED = "COMPUTATION_FAILED"
    NOT_FOUND = "NOT_FOUND"
    INTEGRITY_ERROR = "INTEGRITY_ERROR"


# HTTP status mapping used by the FastAPI layer. Kept here so the mapping has
# exactly one definition.
HTTP_STATUS = {
    ErrorCode.INPUT_ERROR: 400,
    ErrorCode.STATE_CONFLICT: 409,
    ErrorCode.RESOURCE_EXHAUSTED: 413,
    ErrorCode.COMPUTATION_FAILED: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTEGRITY_ERROR: 500,
}


class DomainError(Exception):
    """Base class for all expected, classified service errors."""

    code: str = ErrorCode.COMPUTATION_FAILED

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class InputValidationError(DomainError):
    code = ErrorCode.INPUT_ERROR


class StateConflictError(DomainError):
    code = ErrorCode.STATE_CONFLICT


class ResourceExhaustedError(DomainError):
    code = ErrorCode.RESOURCE_EXHAUSTED


class ComputationError(DomainError):
    code = ErrorCode.COMPUTATION_FAILED


class NotFoundError(DomainError):
    code = ErrorCode.NOT_FOUND


class IntegrityError(DomainError):
    code = ErrorCode.INTEGRITY_ERROR
