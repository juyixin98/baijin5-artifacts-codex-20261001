"""Error taxonomy for the filtering service.

Every failure raised by the service is a ``FilterServiceError`` carrying a
stable ``category`` so clients and tests can distinguish:

- ``input_error``         — malformed coefficients, non-finite samples, bad shapes
- ``not_found``           — unknown stream id
- ``state_conflict``      — parameter-version mismatch / concurrent state conflict
- ``resource_exhausted``  — configured limits exceeded (streams/channels/sections/chunk)
- ``computation_failure`` — numerical failure during filtering (non-finite output)

The API layer maps each category to an HTTP status and an error envelope;
the DSP layer never silently swallows or zeroes a failed computation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    INPUT_ERROR = "input_error"
    NOT_FOUND = "not_found"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTATION_FAILURE = "computation_failure"


class FilterServiceError(Exception):
    """Base class for all service errors."""

    category: ErrorCategory = ErrorCategory.INPUT_ERROR
    http_status: int = 400

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}

    def envelope(self) -> dict[str, Any]:
        return {
            "error": {
                "category": self.category.value,
                "message": self.message,
                "detail": self.detail,
            }
        }


class CoefficientValidationError(FilterServiceError):
    """Coefficients failed normalization or stability checks."""

    category = ErrorCategory.INPUT_ERROR
    http_status = 422


class SampleValidationError(FilterServiceError):
    """Input samples are malformed or non-finite."""

    category = ErrorCategory.INPUT_ERROR
    http_status = 422


class StreamNotFoundError(FilterServiceError):
    category = ErrorCategory.NOT_FOUND
    http_status = 404


class VersionConflictError(FilterServiceError):
    """Caller-supplied expected version does not match the stream's version."""

    category = ErrorCategory.STATE_CONFLICT
    http_status = 409


class ResourceLimitError(FilterServiceError):
    category = ErrorCategory.RESOURCE_EXHAUSTED
    http_status = 413


class ComputationError(FilterServiceError):
    """Numerical failure (non-finite output/state). Never silently zeroed."""

    category = ErrorCategory.COMPUTATION_FAILURE
    http_status = 500
