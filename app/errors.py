"""Error taxonomy for the SOS IIR filter service.

Every failure raised by the service carries a stable ``category`` so that
clients and tests can distinguish:

- ``input_error``        -> malformed samples / coefficients (HTTP 422)
- ``not_found``          -> unknown stream id (HTTP 404)
- ``state_conflict``     -> parameter-version mismatch (HTTP 409)
- ``resource_exhausted`` -> configured limits exceeded (HTTP 429)
- ``computation_failed`` -> numerical failure during filtering (HTTP 500)
"""

from __future__ import annotations

import enum


class ErrorCategory(str, enum.Enum):
    INPUT = "input_error"
    NOT_FOUND = "not_found"
    STATE_CONFLICT = "state_conflict"
    RESOURCE = "resource_exhausted"
    COMPUTATION = "computation_failed"


class FilterServiceError(Exception):
    """Base class for all service errors.

    Attributes:
        category: machine-readable failure class.
        code: stable snake-case identifier of the specific failure.
        http_status: status code the API layer maps this error to.
    """

    category: ErrorCategory = ErrorCategory.COMPUTATION
    code: str = "internal_error"
    http_status: int = 500

    def __init__(self, message: str, *, context: dict | None = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}


# --- input errors (422) -----------------------------------------------------


class CoefficientError(FilterServiceError):
    """Coefficients failed normalization or the stability check."""

    category = ErrorCategory.INPUT
    code = "invalid_coefficients"
    http_status = 422


class SampleBlockError(FilterServiceError):
    """Sample block is malformed (shape, dtype, non-finite values)."""

    category = ErrorCategory.INPUT
    code = "invalid_sample_block"
    http_status = 422


# --- lookup / state errors ---------------------------------------------------


class StreamNotFoundError(FilterServiceError):
    category = ErrorCategory.NOT_FOUND
    code = "stream_not_found"
    http_status = 404


class ParamVersionConflictError(FilterServiceError):
    """Caller-supplied parameter version does not match the stream's."""

    category = ErrorCategory.STATE_CONFLICT
    code = "param_version_conflict"
    http_status = 409


# --- resource errors (429) ---------------------------------------------------


class ResourceLimitError(FilterServiceError):
    category = ErrorCategory.RESOURCE
    code = "resource_limit_exceeded"
    http_status = 429


# --- computation errors (500) ------------------------------------------------


class NonFiniteOutputError(FilterServiceError):
    """Filtering produced NaN/Inf. Never silently zeroed: the block fails."""

    category = ErrorCategory.COMPUTATION
    code = "non_finite_output"
    http_status = 500
