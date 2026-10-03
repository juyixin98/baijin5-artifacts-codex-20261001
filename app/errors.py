"""Error taxonomy for the seam carving service.

Every domain failure maps to a stable ``category`` string so that API
clients and tests can assert on the failure class instead of parsing
messages.  Unexpected exceptions are surfaced as ``INTERNAL`` and never
reported as success.
"""

from __future__ import annotations


class SeamCarveError(Exception):
    """Base class for all expected domain errors."""

    category: str = "INTERNAL"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


class InvalidImageError(SeamCarveError):
    category = "INVALID_IMAGE"
    http_status = 422


class MaskShapeError(SeamCarveError):
    category = "MASK_SHAPE_MISMATCH"
    http_status = 422


class InvalidRequestError(SeamCarveError):
    category = "INVALID_REQUEST"
    http_status = 422


class InvalidConfigError(SeamCarveError):
    category = "INVALID_CONFIG"
    http_status = 422


class NoLegalSeamError(SeamCarveError):
    """Raised when the protection mask blocks every possible seam."""

    category = "NO_LEGAL_SEAM"
    http_status = 422


class JobNotFoundError(SeamCarveError):
    category = "JOB_NOT_FOUND"
    http_status = 404
