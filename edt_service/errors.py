"""Failure taxonomy.

Every layer raises :class:`EdtError` (or a subclass) with a stable
``category`` string so API responses and logs can report *why* something
failed instead of merely *that* it failed.
"""

from __future__ import annotations


class ErrorCategory:
    """Stable machine-readable failure categories."""

    INVALID_GRID = "INVALID_GRID"
    INVALID_SPACING = "INVALID_SPACING"
    INVALID_OPTION = "INVALID_OPTION"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class EdtError(Exception):
    """Base error carrying a category and an HTTP status code."""

    def __init__(self, category: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.category = category
        self.message = message
        self.status_code = status_code


class InvalidGridError(EdtError):
    def __init__(self, message: str):
        super().__init__(ErrorCategory.INVALID_GRID, message, status_code=422)


class InvalidSpacingError(EdtError):
    def __init__(self, message: str):
        super().__init__(ErrorCategory.INVALID_SPACING, message, status_code=422)


class InvalidOptionError(EdtError):
    def __init__(self, message: str):
        super().__init__(ErrorCategory.INVALID_OPTION, message, status_code=422)


class PayloadTooLargeError(EdtError):
    def __init__(self, message: str):
        super().__init__(ErrorCategory.PAYLOAD_TOO_LARGE, message, status_code=413)
