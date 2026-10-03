"""Error taxonomy for the pyramid service.

Every failure raised by the service is a :class:`PyramidError` carrying a
machine-readable ``category`` and an ``http_status``.  The four required
categories — input errors, state conflicts, resource exhaustion and compute
failures — are kept distinguishable end-to-end (logs, API responses, tests).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional


class ErrorCategory(str, Enum):
    INPUT = "input_error"
    NOT_FOUND = "not_found"
    STATE_CONFLICT = "state_conflict"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    COMPUTE = "compute_failure"


class PyramidError(Exception):
    """Base class for all service errors."""

    category: ErrorCategory = ErrorCategory.COMPUTE
    http_status: int = 500

    def __init__(self, message: str, *, detail: Optional[Any] = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_payload(self) -> dict:
        return {
            "error": {
                "category": self.category.value,
                "message": self.message,
                "detail": self.detail,
            }
        }


class InputValidationError(PyramidError):
    """Caller supplied an invalid parameter (bad region, bad pattern, ...)."""

    category = ErrorCategory.INPUT
    http_status = 400


class NotFoundError(PyramidError):
    """Requested pyramid / level / tile does not exist."""

    category = ErrorCategory.NOT_FOUND
    http_status = 404


class StateConflictError(PyramidError):
    """Request conflicts with existing state (e.g. re-publishing a level)."""

    category = ErrorCategory.STATE_CONFLICT
    http_status = 409


class ResourceExhaustedError(PyramidError):
    """Request exceeds a configured resource limit (pixels, size)."""

    category = ErrorCategory.RESOURCE_EXHAUSTED
    http_status = 507


class ComputeError(PyramidError):
    """A numeric or internal computation failed."""

    category = ErrorCategory.COMPUTE
    http_status = 500


class TileIntegrityError(ComputeError):
    """A stored tile or manifest failed integrity verification.

    Raised instead of ever substituting placeholder (e.g. all-black) pixels.
    """
