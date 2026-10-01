"""Typed error contract.

Every failure the service can produce is one of the :class:`ErrorCode`
values below.  Categories are coarse (so callers can branch on them) while
codes are stable strings for logs and tests.  The same category/code pair is
used by the kernel, the store and the HTTP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    INPUT = "INPUT"
    NOT_FOUND = "NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    COMPUTATION_FAILED = "COMPUTATION_FAILED"


@dataclass(frozen=True, slots=True)
class ErrorCode:
    code: str
    category: ErrorCategory
    http_status: int
    message: str


# Input errors (the submitted request / value is invalid in isolation).
INVALID_PVALUE = ErrorCode(
    "E1001", ErrorCategory.INPUT, 422,
    "p_value must be a finite real number in the closed interval [0, 1].",
)
INVALID_HYPOTHESIS_ID = ErrorCode(
    "E1002", ErrorCategory.INPUT, 422,
    "hypothesis_id must be 1-256 characters of non-whitespace text.",
)
FROZEN_PARAMETER = ErrorCode(
    "E1003", ErrorCategory.INPUT, 422,
    "Rule parameters are frozen; the submitted parameters do not match the "
    "frozen LORD 3 contract.",
)
MALFORMED_REQUEST = ErrorCode(
    "E1004", ErrorCategory.INPUT, 422,
    "Request body is malformed or contains unknown fields.",
)
INVALID_QUERY = ErrorCode(
    "E1005", ErrorCategory.INPUT, 422,
    "Query parameter is outside its allowed range.",
)

# State conflicts (the request itself is well-formed but history forbids it).
RUN_NOT_FOUND = ErrorCode(
    "E1020", ErrorCategory.NOT_FOUND, 404, "Unknown run_id.",
)
DUPLICATE_HYPOTHESIS = ErrorCode(
    "E1021", ErrorCategory.STATE_CONFLICT, 409,
    "This hypothesis_id already has an irrevocable decision in the run; "
    "past decisions and spent budget cannot be rewritten.",
)

# Evidence integrity (stored history fails its own audit).
EVIDENCE_TAMPERED = ErrorCode(
    "E1030", ErrorCategory.EVIDENCE_INTEGRITY, 500,
    "Stored evidence fails hash-chain or replay verification.",
)

# Resource / capacity.
RUN_LIMIT_REACHED = ErrorCode(
    "E1040", ErrorCategory.RESOURCE_EXHAUSTED, 507,
    "The run reached its configured maximum number of decisions.",
)

# Numerical failures inside the kernel.
NONFINITE_RESULT = ErrorCode(
    "E1050", ErrorCategory.COMPUTATION_FAILED, 500,
    "A threshold or wealth update produced a non-finite value.",
)


class LORD3Error(Exception):
    """Base class for all categorized service errors."""

    error_code: ErrorCode = MALFORMED_REQUEST

    def __init__(self, details: dict | None = None) -> None:
        super().__init__(self.error_code.message)
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "error": {
                "code": self.error_code.code,
                "category": self.error_code.category.value,
                "message": self.error_code.message,
                "details": self.details,
            }
        }


class InvalidPValueError(LORD3Error):
    error_code = INVALID_PVALUE


class InvalidHypothesisIdError(LORD3Error):
    error_code = INVALID_HYPOTHESIS_ID


class FrozenParameterError(LORD3Error):
    error_code = FROZEN_PARAMETER


class MalformedRequestError(LORD3Error):
    error_code = MALFORMED_REQUEST


class InvalidQueryError(LORD3Error):
    error_code = INVALID_QUERY


class RunNotFoundError(LORD3Error):
    error_code = RUN_NOT_FOUND


class DuplicateHypothesisError(LORD3Error):
    error_code = DUPLICATE_HYPOTHESIS


class EvidenceTamperedError(LORD3Error):
    error_code = EVIDENCE_TAMPERED


class RunLimitReachedError(LORD3Error):
    error_code = RUN_LIMIT_REACHED


class NonFiniteResultError(LORD3Error):
    error_code = NONFINITE_RESULT
