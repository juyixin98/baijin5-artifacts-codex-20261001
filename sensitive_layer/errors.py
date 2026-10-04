"""Typed application errors.

The ``category`` strings are part of the public API contract: tests and the
independent verifier assert on them, so a failure is classified, not just
reported as "the endpoint returned an error".
"""
from __future__ import annotations


class AppError(Exception):
    http_status = 400
    category = "internal_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ValidationError(AppError):
    http_status = 422
    category = "validation_error"


class UnknownPurposeError(AppError):
    http_status = 422
    category = "unknown_purpose"


class NullNotIndexableError(AppError):
    http_status = 422
    category = "null_not_indexable"


class RecordNotFoundError(AppError):
    http_status = 404
    category = "record_not_found"


class RotationConflictError(AppError):
    http_status = 409
    category = "rotation_conflict"


class PlaintextReadDisabledError(AppError):
    http_status = 403
    category = "plaintext_read_disabled"
