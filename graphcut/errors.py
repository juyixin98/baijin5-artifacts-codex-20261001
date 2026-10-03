"""Error taxonomy for the graph-cut segmentation service.

Every failure raised by the service is a ``GraphCutError`` carrying a
machine-readable ``category`` so that callers (and test logs) can distinguish:

- ``input``              -> malformed contracts, negative terms, non-submodular
                             potentials, unknown references (HTTP 400/404)
- ``state_conflict``     -> mutually exclusive constraints, illegal job-state
                             transitions (HTTP 409)
- ``resource_exhausted`` -> configured pixel/edge/numerical-range limits
                             exceeded (HTTP 413)
- ``computation``        -> solver or certificate failures after inputs were
                             accepted (HTTP 500)

The API layer maps these to HTTP responses; the numeric kernel never raises
bare ``ValueError`` across module boundaries.
"""

from __future__ import annotations

import enum
from typing import Any


class ErrorCategory(str, enum.Enum):
    INPUT = "input"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION = "computation"


class GraphCutError(Exception):
    """Base class for all service errors.

    Attributes:
        category: one of :class:`ErrorCategory`; drives HTTP status mapping.
        code: stable snake_case identifier, e.g. ``non_submodular_pairwise``.
        message: human-readable explanation including the judgment reason.
        details: structured context (offending indices, values, ...).
    """

    category: ErrorCategory = ErrorCategory.COMPUTATION
    http_status: int = 500

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
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


class InputValidationError(GraphCutError):
    category = ErrorCategory.INPUT
    http_status = 400


class NotFoundError(GraphCutError):
    """Unknown job / fixture reference. Still an input-side problem."""

    category = ErrorCategory.INPUT
    http_status = 404


class StateConflictError(GraphCutError):
    category = ErrorCategory.STATE_CONFLICT
    http_status = 409


class ResourceExhaustedError(GraphCutError):
    category = ErrorCategory.RESOURCE_EXHAUSTED
    http_status = 413


class ComputationError(GraphCutError):
    category = ErrorCategory.COMPUTATION
    http_status = 500
