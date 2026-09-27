"""Error taxonomy for the entity resolution backend.

Every failure raised by the system is one of four distinguishable categories:

- INPUT_ERROR: the caller supplied invalid data (bad record, unknown id).
- STATE_CONFLICT: the request conflicts with persisted state
  (must-link / cannot-link contradiction, lock violation).
- RESOURCE_EXHAUSTED: a configured budget was exceeded
  (too many records, too many candidate pairs).
- COMPUTATION_FAILURE: an internal invariant broke (replay mismatch, store error).

The API layer maps categories to HTTP status codes; tests assert on categories
so failure classes stay distinguishable end to end.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ERCategory(str, Enum):
    INPUT_ERROR = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"


class ERError(Exception):
    """Base class for all backend errors."""

    category: ERCategory = ERCategory.COMPUTATION_FAILURE
    code: str = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class InputValidationError(ERError):
    category = ERCategory.INPUT_ERROR
    code = "input_validation_failed"


class ConstraintConflictError(ERError):
    category = ERCategory.STATE_CONFLICT
    code = "constraint_conflict"


class ResourceExhaustedError(ERError):
    category = ERCategory.RESOURCE_EXHAUSTED
    code = "resource_exhausted"


class ComputationError(ERError):
    category = ERCategory.COMPUTATION_FAILURE
    code = "computation_failed"
