"""Typed error categories.

Every failure raised by the package carries a stable category so the API
layer and tests can assert on the *kind* of failure instead of matching
message text. Unknown exceptions are never silently mapped to success.
"""

from __future__ import annotations

import enum


class ErrorCategory(str, enum.Enum):
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"  # malformed image/mask/request semantics
    NO_LEGAL_SEAM = "NO_LEGAL_SEAM"            # protection makes every seam illegal
    REQUEST_VALIDATION = "REQUEST_VALIDATION"  # schema-level rejection (pydantic)
    INTERNAL_ERROR = "INTERNAL_ERROR"          # anything unexpected


class SeamCarveError(Exception):
    """Base class for all expected, categorised failures."""

    category: ErrorCategory = ErrorCategory.INTERNAL_ERROR

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class ContractViolationError(SeamCarveError):
    """Input violates the image data contract (shape, dtype, range, mask)."""

    category = ErrorCategory.CONTRACT_VIOLATION


class NoLegalSeamError(SeamCarveError):
    """No seam exists that respects the protected region."""

    category = ErrorCategory.NO_LEGAL_SEAM
