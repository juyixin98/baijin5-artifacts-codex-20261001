"""Error taxonomy for the color conversion backend.

Every rejection or failure carries a stable :class:`FailureCategory` so that
API clients and tests can assert on the *kind* of failure, not just on the
fact that something failed.
"""
from __future__ import annotations

import enum
from typing import Any


class FailureCategory(str, enum.Enum):
    """Stable machine-readable failure categories."""

    PROFILE_MISSING = "profile_missing"
    PROFILE_CORRUPT = "profile_corrupt"
    PROFILE_HASH_MISMATCH = "profile_hash_mismatch"
    PROFILE_COLORSPACE_MISMATCH = "profile_colorspace_mismatch"
    PROFILE_ROLE_NOT_ALLOWED = "profile_role_not_allowed"
    CMYK_RESTRICTED = "cmyk_restricted"
    CONTRACT_VIOLATION = "contract_violation"
    UNSUPPORTED_INTENT = "unsupported_intent"
    IMAGE_DECODE_FAILED = "image_decode_failed"
    EMBEDDED_PROFILE_ABSENT = "embedded_profile_absent"
    LIMIT_EXCEEDED = "limit_exceeded"
    ENGINE_ERROR = "engine_error"


class ConversionError(Exception):
    """Base error carrying a :class:`FailureCategory`."""

    def __init__(
        self,
        category: FailureCategory,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = dict(detail or {})


class ProfileError(ConversionError):
    """Profile resolution or validation failed."""


class ContractError(ConversionError):
    """Image data contract violation."""


class EngineError(ConversionError):
    """The underlying ICC engine failed at runtime."""
