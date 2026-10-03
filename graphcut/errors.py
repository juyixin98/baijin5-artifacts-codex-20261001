"""Typed error taxonomy for the graph-cut segmentation service.

Every failure raised by the service carries a stable ``category`` so that
callers (and tests) can distinguish:

- ``INPUT_VALIDATION``   -> malformed or semantically invalid request data
- ``STATE_CONFLICT``     -> the request conflicts with current state
                            (e.g. contradictory hard seeds, job already done)
- ``RESOURCE_EXHAUSTED`` -> the request exceeds configured capacity limits
- ``COMPUTATION_FAILURE``-> the numerical kernel or its certificate failed
- ``NOT_FOUND``          -> referenced job does not exist

The HTTP mapping lives in ``graphcut.main``; the core never imports HTTP.
"""

from __future__ import annotations

import enum
from typing import Any


class ErrorCategory(str, enum.Enum):
    INPUT_VALIDATION = "INPUT_VALIDATION"
    STATE_CONFLICT = "STATE_CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    COMPUTATION_FAILURE = "COMPUTATION_FAILURE"
    NOT_FOUND = "NOT_FOUND"


class GraphCutError(Exception):
    """Base class for all service errors.

    Attributes:
        category: coarse failure class, stable across releases.
        code: machine-readable fine-grained code (e.g. ``NON_SUBMODULAR_POTENTIAL``).
        message: human-readable explanation.
        details: structured context useful for replaying the failure.
    """

    category: ErrorCategory = ErrorCategory.COMPUTATION_FAILURE
    code: str = "INTERNAL"

    def __init__(self, message: str, *, code: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.message = message
        self.details: dict[str, Any] = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class InputValidationError(GraphCutError):
    category = ErrorCategory.INPUT_VALIDATION
    code = "INPUT_VALIDATION"


class StateConflictError(GraphCutError):
    category = ErrorCategory.STATE_CONFLICT
    code = "STATE_CONFLICT"


class ResourceExhaustedError(GraphCutError):
    category = ErrorCategory.RESOURCE_EXHAUSTED
    code = "RESOURCE_EXHAUSTED"


class ComputationError(GraphCutError):
    category = ErrorCategory.COMPUTATION_FAILURE
    code = "COMPUTATION_FAILURE"


class NotFoundError(GraphCutError):
    category = ErrorCategory.NOT_FOUND
    code = "NOT_FOUND"
