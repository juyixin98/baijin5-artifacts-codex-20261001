"""Error taxonomy for the aggregation service.

Every failure raised by the service carries an explicit category so that
callers (and tests) can assert on the *kind* of failure instead of only
checking that "something went wrong".  The API layer maps categories to
HTTP status codes; unknown exceptions are never reported as success.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    # --- protocol encoding layer ---
    PLAINTEXT_OUT_OF_RANGE = "PLAINTEXT_OUT_OF_RANGE"
    COEFFICIENT_OUT_OF_RANGE = "COEFFICIENT_OUT_OF_RANGE"
    ENCODING_PARAM_INVALID = "ENCODING_PARAM_INVALID"
    # --- crypto adapter layer ---
    KEY_MISMATCH = "KEY_MISMATCH"
    CIPHERTEXT_INVALID = "CIPHERTEXT_INVALID"
    # --- state layer ---
    BATCH_NOT_FOUND = "BATCH_NOT_FOUND"
    BATCH_STATE_INVALID = "BATCH_STATE_INVALID"
    AGGREGATE_BOUND_EXCEEDED = "AGGREGATE_BOUND_EXCEEDED"
    # --- decode layer: modular wraparound must NOT be read as a plain negative ---
    DECODE_AMBIGUOUS = "DECODE_AMBIGUOUS"
    # --- verification layer ---
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    VERIFICATION_INCOMPLETE = "VERIFICATION_INCOMPLETE"


_HTTP_STATUS = {
    ErrorCategory.PLAINTEXT_OUT_OF_RANGE: 422,
    ErrorCategory.COEFFICIENT_OUT_OF_RANGE: 422,
    ErrorCategory.ENCODING_PARAM_INVALID: 422,
    ErrorCategory.KEY_MISMATCH: 400,
    ErrorCategory.CIPHERTEXT_INVALID: 400,
    ErrorCategory.BATCH_NOT_FOUND: 404,
    ErrorCategory.BATCH_STATE_INVALID: 409,
    ErrorCategory.AGGREGATE_BOUND_EXCEEDED: 409,
    ErrorCategory.DECODE_AMBIGUOUS: 409,
    ErrorCategory.VERIFICATION_FAILED: 409,
    ErrorCategory.VERIFICATION_INCOMPLETE: 409,
}


class PaillierServiceError(Exception):
    """Structured service error with a machine-readable category."""

    def __init__(self, category: ErrorCategory, message: str, detail: dict | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail or {}

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS.get(self.category, 500)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }
