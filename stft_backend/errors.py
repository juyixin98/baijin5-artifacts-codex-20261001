"""Domain error codes and exception hierarchy.

Every failure raised by the backend carries a stable ``ErrorCode`` so that
clients (and tests) can assert the *category* of a failure instead of
matching on human-readable text.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Stable machine-readable failure categories."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    UNSUPPORTED_WINDOW = "UNSUPPORTED_WINDOW"
    WINDOW_LENGTH_MISMATCH = "WINDOW_LENGTH_MISMATCH"
    NFFT_TOO_SMALL = "NFFT_TOO_SMALL"
    # Window/hop combination cannot be reconstructed (NOLA violated or gaps).
    NOLA_VIOLATION = "NOLA_VIOLATION"
    SPECTRUM_SHAPE_MISMATCH = "SPECTRUM_SHAPE_MISMATCH"
    ASYMMETRIC_SPECTRUM = "ASYMMETRIC_SPECTRUM"
    NON_FINITE_SIGNAL = "NON_FINITE_SIGNAL"
    # OLA normalization denominator is zero inside the requested region.
    UNCOVERED_SAMPLES = "UNCOVERED_SAMPLES"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_DIRECTION_CONFLICT = "SESSION_DIRECTION_CONFLICT"
    FRAME_SEQUENCE_ERROR = "FRAME_SEQUENCE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Default HTTP status per category. All are client errors except INTERNAL.
_HTTP_STATUS = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.INVALID_PARAMETER: 422,
    ErrorCode.UNSUPPORTED_WINDOW: 422,
    ErrorCode.WINDOW_LENGTH_MISMATCH: 422,
    ErrorCode.NFFT_TOO_SMALL: 422,
    ErrorCode.NOLA_VIOLATION: 422,
    ErrorCode.SPECTRUM_SHAPE_MISMATCH: 422,
    ErrorCode.ASYMMETRIC_SPECTRUM: 422,
    ErrorCode.NON_FINITE_SIGNAL: 422,
    ErrorCode.UNCOVERED_SAMPLES: 422,
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.SESSION_DIRECTION_CONFLICT: 409,
    ErrorCode.FRAME_SEQUENCE_ERROR: 409,
    ErrorCode.INTERNAL_ERROR: 500,
}


class StftError(Exception):
    """Base class for every domain error raised by the backend."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        stage: str = "unknown",
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.details = details or {}

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self.code]

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "stage": self.stage,
            "details": self.details,
        }
