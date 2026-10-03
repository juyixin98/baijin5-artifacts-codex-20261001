"""Explicit error taxonomy.

Every failure mode maps to a distinct exception type with a stable machine
readable ``code`` and an HTTP status. The service layer never converts an
unknown/exceptional state into a success response: anything that is not an
expected result raises one of these (or propagates as a 500).
"""

from __future__ import annotations


class MFCCError(Exception):
    """Base class for all expected backend errors."""

    code = "MFCC_ERROR"
    http_status = 500

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"code": self.code, "message": str(self), "detail": self.detail}


class ConfigError(MFCCError):
    """The feature configuration is internally inconsistent or out of range."""

    code = "INVALID_CONFIG"
    http_status = 422


class EmptyFilterSupportError(ConfigError):
    """A mel filter has no FFT bin strictly inside its triangular support.

    This happens when the analysis resolution (sample_rate / nfft) is too
    coarse for the requested number of mel bands, so the filter would be
    all-zero and silently produce -inf/constant energies.
    """

    code = "EMPTY_FILTER_SUPPORT"
    http_status = 422


class InvalidAudioError(MFCCError):
    """The sample buffer itself is malformed (empty, non-1D, NaN/Inf)."""

    code = "INVALID_AUDIO"
    http_status = 400


class InsufficientSignalError(InvalidAudioError):
    """Fewer samples than one analysis frame; no feature can be computed."""

    code = "INSUFFICIENT_SIGNAL"
    http_status = 422


class SessionStateError(MFCCError):
    """Streaming session lifecycle violation (unknown id, chunk after finish)."""

    code = "SESSION_STATE_ERROR"
    http_status = 409


class SessionNotFoundError(SessionStateError):
    code = "SESSION_NOT_FOUND"
    http_status = 404
