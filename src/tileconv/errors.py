"""Domain error categories.

Every expected failure raises a subclass of :class:`TileConvError` carrying a
stable ``category`` string. The category is surfaced in logs, validation
reports and API error responses so callers can distinguish failure classes
instead of parsing messages. Unknown exceptions are never re-labelled as
success: the API layer maps them to the ``InternalError`` category with HTTP
500.
"""

from __future__ import annotations

from typing import Any, Optional


class TileConvError(Exception):
    """Base class for all expected, categorised failures."""

    category = "InternalError"
    http_status = 500

    def __init__(self, message: str, context: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.message,
            "context": self.context,
        }


class InvalidSpecError(TileConvError):
    """A kernel/image/job specification failed validation."""

    category = "InvalidSpec"
    http_status = 422


class NotFoundError(TileConvError):
    """Referenced image, kernel or job does not exist."""

    category = "NotFound"
    http_status = 404


class DigestMismatchError(TileConvError):
    """Input image or kernel digest does not match the digest bound to a job."""

    category = "DigestMismatch"
    http_status = 409


class JobStateError(TileConvError):
    """Job state does not allow the requested transition."""

    category = "JobStateError"
    http_status = 409


class InjectedInterrupt(TileConvError):
    """Deterministic interrupt injected via ``fail_after`` for resume testing."""

    category = "InterruptInjected"
    http_status = 500


class ValidationFailedError(TileConvError):
    """Tiled output deviates from the direct reference beyond tolerance."""

    category = "ValidationFailed"
    http_status = 500
