"""Explicit error taxonomy.

Every failure path raises a :class:`MiniseedError` with a stable ``code`` so
that API responses and tests can assert on the *failure category* instead of
matching free-text messages. Unknown/unexpected exceptions are never silently
mapped to success -- the API layer converts them to ``INTERNAL_ERROR``.
"""
from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # Input validation (400)
    EMPTY_SEQUENCE = "EMPTY_SEQUENCE"
    INVALID_CHARACTER = "INVALID_CHARACTER"
    SEQUENCE_TOO_SHORT = "SEQUENCE_TOO_SHORT"
    INVALID_PARAMETER = "INVALID_PARAMETER"
    PARAMETER_CONFLICT = "PARAMETER_CONFLICT"
    # Index state (404/409)
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    RUN_ALREADY_EXISTS = "RUN_ALREADY_EXISTS"
    EMPTY_INDEX = "EMPTY_INDEX"
    # Capacity guards (422)
    BUCKET_OVERFLOW = "BUCKET_OVERFLOW"
    TOO_MANY_CANDIDATES = "TOO_MANY_CANDIDATES"
    # Anything not anticipated (500) -- not a success alias.
    INTERNAL_ERROR = "INTERNAL_ERROR"


# Which HTTP status each category maps to.
HTTP_STATUS = {
    ErrorCode.EMPTY_SEQUENCE: 400,
    ErrorCode.INVALID_CHARACTER: 400,
    ErrorCode.SEQUENCE_TOO_SHORT: 400,
    ErrorCode.INVALID_PARAMETER: 400,
    ErrorCode.PARAMETER_CONFLICT: 400,
    ErrorCode.RUN_NOT_FOUND: 404,
    ErrorCode.RUN_ALREADY_EXISTS: 409,
    ErrorCode.EMPTY_INDEX: 409,
    ErrorCode.BUCKET_OVERFLOW: 422,
    ErrorCode.TOO_MANY_CANDIDATES: 422,
    ErrorCode.INTERNAL_ERROR: 500,
}


class MiniseedError(Exception):
    """Domain error carrying a stable category code and context."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        context: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code.value,
                "message": self.message,
                "context": self.context,
            },
        }
