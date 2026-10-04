"""Explicit error categories.

Errors are never silently converted into success responses. Every failure maps
to a stable :class:`ErrorCode` that is surfaced on the API and written to logs.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    # Input / parsing
    EMPTY_SEQUENCE = "EMPTY_SEQUENCE"
    SEQUENCE_TOO_LONG = "SEQUENCE_TOO_LONG"
    UNSUPPORTED_RESIDUE = "UNSUPPORTED_RESIDUE"
    INVALID_TERMINAL_MOD = "INVALID_TERMINAL_MOD"
    INVALID_VARIABLE_MOD = "INVALID_VARIABLE_MOD"
    MOD_FORM_LIMIT = "MOD_FORM_LIMIT"
    INVALID_CHARGE = "INVALID_CHARGE"
    INVALID_MISSED_CLEAVAGES = "INVALID_MISSED_CLEAVAGES"
    INVALID_ENZYME = "INVALID_ENZYME"
    INVALID_CUSTOM_RULE = "INVALID_CUSTOM_RULE"
    MOD_TARGET_UNKNOWN_RESIDUE = "MOD_TARGET_UNKNOWN_RESIDUE"

    # Processing
    DIGEST_FAILED = "DIGEST_FAILED"
    MASS_UNCERTAIN = "MASS_UNCERTAIN"

    # Storage / lookup
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    STORAGE_ERROR = "STORAGE_ERROR"
    VALIDATION_NOT_PENDING = "VALIDATION_NOT_PENDING"


class DigestError(Exception):
    """Domain error carrying a stable category and positional context."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        position: int | None = None,
        residue: str | None = None,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.position = position  # 1-based position in the parent sequence
        self.residue = residue
        self.detail = detail or {}

    def to_dict(self) -> dict:
        payload = {
            "error": self.code.value,
            "message": self.message,
        }
        if self.position is not None:
            payload["position"] = self.position
        if self.residue is not None:
            payload["residue"] = self.residue
        if self.detail:
            payload["detail"] = self.detail
        return payload
