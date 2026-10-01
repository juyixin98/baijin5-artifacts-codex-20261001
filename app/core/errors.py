"""Typed error taxonomy for the estimation pipeline.

Failures are never folded into a generic success. Every validation or
estimation failure carries a stable ``ErrorCode`` string so that API clients
and test assertions can distinguish failure *categories*.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    EMPTY_DATA = "EMPTY_DATA"
    TREATMENT_NOT_BINARY = "TREATMENT_NOT_BINARY"
    GROUP_EMPTY = "GROUP_EMPTY"
    DUPLICATE_UNIT = "DUPLICATE_UNIT"
    OUTCOME_MISSING = "OUTCOME_MISSING"
    COVARIATE_VALUE_MISSING = "COVARIATE_VALUE_MISSING"
    ZERO_VARIANCE_COVARIATE = "ZERO_VARIANCE_COVARIATE"
    UNDECLARED_COVARIATE = "UNDECLARED_COVARIATE"
    LEAKED_COVARIATE = "LEAKED_COVARIATE"
    THETA_SOURCE_UNAVAILABLE = "THETA_SOURCE_UNAVAILABLE"
    INSUFFICIENT_SOURCE_SAMPLE = "INSUFFICIENT_SOURCE_SAMPLE"
    RANK_DEFICIENT_DESIGN = "RANK_DEFICIENT_DESIGN"
    UNKNOWN_EXPERIMENT = "UNKNOWN_EXPERIMENT"
    UNKNOWN_RUN = "UNKNOWN_RUN"
    SCHEMA_CONFLICT = "SCHEMA_CONFLICT"
    INVALID_REQUEST = "INVALID_REQUEST"


class EstimationError(Exception):
    """Base error for all validation / estimation failures."""

    def __init__(self, code: ErrorCode, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = ErrorCode(code)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "details": self.details}


class ValidationError(EstimationError):
    """Input contract violations (missing values, leakage, bad groups)."""


class EstimationFailure(EstimationError):
    """Numerical / design-matrix failures at estimation time."""
