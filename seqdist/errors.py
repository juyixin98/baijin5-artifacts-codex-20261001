"""Typed error taxonomy.

Every failure crossing a module boundary carries an ErrorCategory so that
callers (API layer, tests, operators) can distinguish:

    INPUT_ERROR          -- malformed or inconsistent user input
    STATE_CONFLICT       -- conflict with persisted state (e.g. duplicate run id)
    RESOURCE_EXHAUSTED   -- configured limits exceeded (length, replicates)
    COMPUTATION_FAILURE  -- the computation itself cannot produce a value
    NOT_FOUND            -- referenced persisted object does not exist
"""
from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    INPUT_ERROR = "input_error"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"
    NOT_FOUND = "not_found"


class SeqDistError(Exception):
    """Base error carrying a machine-readable category and optional detail."""

    category: ErrorCategory = ErrorCategory.COMPUTATION_FAILURE

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "category": self.category.value,
                "message": self.message,
                "detail": self.detail,
            }
        }


class InputValidationError(SeqDistError):
    category = ErrorCategory.INPUT_ERROR


class StateConflictError(SeqDistError):
    category = ErrorCategory.STATE_CONFLICT


class ResourceExhaustedError(SeqDistError):
    category = ErrorCategory.RESOURCE_EXHAUSTED


class ComputationError(SeqDistError):
    category = ErrorCategory.COMPUTATION_FAILURE


class NoValidSitesError(ComputationError):
    """Raised when no comparable sites remain after filtering."""


class NotFoundError(SeqDistError):
    category = ErrorCategory.NOT_FOUND
