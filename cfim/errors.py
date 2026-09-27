"""Error codes shared across layers.

The kernel and corpus layers raise transport-independent exceptions; the
service layer translates them to HTTP responses. Keeping the codes here means
tests and clients can rely on stable string identifiers.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_TRANSACTION = "INVALID_TRANSACTION"
    INVALID_ITEM = "INVALID_ITEM"
    CORPUS_TOO_LARGE = "CORPUS_TOO_LARGE"
    MIN_SUPPORT_INVALID = "MIN_SUPPORT_INVALID"
    BUDGET_INVALID = "BUDGET_INVALID"
    ITEM_NOT_IN_DOMAIN = "ITEM_NOT_IN_DOMAIN"
    CORPUS_NOT_FOUND = "CORPUS_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    STATE_VERSION_MISMATCH = "STATE_VERSION_MISMATCH"


class DomainError(Exception):
    """Raised by corpus/kernel logic for invalid caller input."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


class ApiError(Exception):
    """Raised by the service layer for HTTP-bound failures."""

    def __init__(
        self,
        status_code: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
