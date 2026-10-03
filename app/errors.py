"""Error taxonomy shared by every module.

Four distinguishable failure categories, per the delivery contract:

- INPUT_VALIDATION:   malformed request / sequence data (caller's fault).
- STATE_CONFLICT:     the request clashes with persisted state (e.g. a
                      client-supplied run_id reused with a different payload).
- RESOURCE_EXHAUSTED: input exceeds configured limits (length, count,
                      bootstrap replicates).
- COMPUTATION_FAILURE: unexpected failure while computing (a bug or an
                      unrecoverable numerical condition). Saturation is NOT
                      in this category - it is a normal per-pair result
                      status, not an exception.
"""

from __future__ import annotations

import enum
from typing import Any


class ErrorCategory(str, enum.Enum):
    INPUT_VALIDATION = "input_validation"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"


class AppError(Exception):
    """Base error carrying a machine-readable category and code."""

    category: ErrorCategory = ErrorCategory.COMPUTATION_FAILURE

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class InputValidationError(AppError):
    category = ErrorCategory.INPUT_VALIDATION


class StateConflictError(AppError):
    category = ErrorCategory.STATE_CONFLICT


class ResourceExhaustedError(AppError):
    category = ErrorCategory.RESOURCE_EXHAUSTED


class ComputationFailureError(AppError):
    category = ErrorCategory.COMPUTATION_FAILURE
