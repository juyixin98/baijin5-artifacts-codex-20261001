"""Typed error categories.

These map 1:1 to ``contract.ErrorCode`` so the API can report *why* a run
did not produce an estimate instead of returning a generic success/failure.
"""
from __future__ import annotations

from app.contract import ErrorCode


class RDPipelineError(Exception):
    error_code = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class InvalidInputError(RDPipelineError):
    error_code = ErrorCode.INVALID_INPUT


class InsufficientDataError(RDPipelineError):
    """Order / support condition fails (too few points on a side).

    This produces a deliberate ``unidentified`` status, *not* an error:
    the request was valid but the design cannot support a local estimate.
    """

    error_code = ErrorCode.INSUFFICIENT_DATA


class SingularFitError(RDPipelineError):
    error_code = ErrorCode.SINGULAR_FIT


class BandwidthFailedError(RDPipelineError):
    error_code = ErrorCode.BANDWIDTH_FAILED
