"""Error taxonomy for the NJ backend.

Every failure raised by the service carries one of these categories so that
callers (and test logs) can distinguish:

- INPUT_VALIDATION:   the request payload is malformed or violates a declared
                      input contract (bad FASTA, asymmetric matrix, ...).
- STATE_CONFLICT:     the request conflicts with persisted state (e.g. an
                      idempotency key replayed with a different payload).
- RESOURCE_EXHAUSTED: the request exceeds a declared resource bound
                      (e.g. more taxa than ``max_taxa``).
- COMPUTATION_FAILED: the numeric pipeline itself could not produce a result
                      (e.g. a negative branch under ``error`` mode).
- NOT_FOUND:          a referenced provenance record does not exist.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    INPUT_VALIDATION = "INPUT_VALIDATION"
    STATE_CONFLICT = "STATE_CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    COMPUTATION_FAILED = "COMPUTATION_FAILED"
    NOT_FOUND = "NOT_FOUND"


class NJServiceError(Exception):
    """Base error carrying a machine-readable category and details."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "details": self.details,
        }


class InputValidationError(NJServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.INPUT_VALIDATION, message, details)


class StateConflictError(NJServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.STATE_CONFLICT, message, details)


class ResourceExhaustedError(NJServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.RESOURCE_EXHAUSTED, message, details)


class ComputationFailedError(NJServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.COMPUTATION_FAILED, message, details)


class NotFoundError(NJServiceError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.NOT_FOUND, message, details)
