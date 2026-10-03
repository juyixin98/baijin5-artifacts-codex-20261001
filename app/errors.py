"""Error taxonomy for the FIR estimation backend.

Every failure raised by the service carries a machine-readable ``code`` so
that callers and test logs can distinguish the four required categories:

- ``INPUT_VALIDATION``      — the request/signal data itself is invalid.
- ``STATE_CONFLICT``        — the stream session is in the wrong state.
- ``RESOURCE_EXHAUSTED``    — the request exceeds configured resource limits.
- ``COMPUTATION_FAILURE``   — the numerical computation failed.
- ``IDENTIFIABILITY_FAILURE`` — the excitation cannot identify the requested
  model order (reported separately because it is a data-property failure,
  not a solver crash); grouped under computation failures for HTTP mapping.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    INPUT_VALIDATION = "INPUT_VALIDATION"
    STATE_CONFLICT = "STATE_CONFLICT"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    COMPUTATION_FAILURE = "COMPUTATION_FAILURE"
    IDENTIFIABILITY_FAILURE = "IDENTIFIABILITY_FAILURE"


class AppError(Exception):
    """Base class for all service errors.

    Attributes:
        code: machine-readable category, see :class:`ErrorCode`.
        reason: short stable slug describing the concrete cause, suitable
            for assertions in tests and for log correlation.
        detail: optional structured context (shapes, limits, ...).
    """

    code: ErrorCode = ErrorCode.COMPUTATION_FAILURE
    http_status: int = 500

    def __init__(self, message: str, *, reason: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "reason": self.reason,
            "message": self.message,
            "detail": self.detail,
        }


class InputError(AppError):
    code = ErrorCode.INPUT_VALIDATION
    http_status = 400


class StateConflictError(AppError):
    code = ErrorCode.STATE_CONFLICT
    http_status = 409


class ResourceExhaustedError(AppError):
    code = ErrorCode.RESOURCE_EXHAUSTED
    http_status = 413


class ComputationError(AppError):
    code = ErrorCode.COMPUTATION_FAILURE
    http_status = 500


class IdentifiabilityError(AppError):
    """Raised when the excitation spectrum is too degenerate to identify the
    requested model order and the caller required identifiability."""

    code = ErrorCode.IDENTIFIABILITY_FAILURE
    http_status = 422
