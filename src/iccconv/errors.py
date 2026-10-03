"""Error taxonomy for the conversion service.

Every failure raised inside iccconv carries a stable ``ErrorCategory`` so
that API responses, diagnostics and tests can assert on the *kind* of
failure instead of matching message text.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCategory(str, Enum):
    MISSING_PROFILE = "missing_profile"
    INVALID_PROFILE = "invalid_profile"
    PROFILE_ROLE_MISMATCH = "profile_role_mismatch"
    UNSUPPORTED_COLORSPACE = "unsupported_colorspace"
    UNSUPPORTED_ALPHA = "unsupported_alpha"
    INTENT_UNSUPPORTED = "intent_unsupported"
    CONTRACT_VIOLATION = "contract_violation"
    ENGINE_FAILURE = "engine_failure"


class ConversionError(Exception):
    """A conversion request cannot be honoured (or only partially)."""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        *,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = ErrorCategory(category)
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }


class MissingProfileError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.MISSING_PROFILE, message, detail=detail)


class InvalidProfileError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.INVALID_PROFILE, message, detail=detail)


class ProfileRoleMismatchError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.PROFILE_ROLE_MISMATCH, message, detail=detail)


class UnsupportedColorSpaceError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.UNSUPPORTED_COLORSPACE, message, detail=detail)


class UnsupportedAlphaError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.UNSUPPORTED_ALPHA, message, detail=detail)


class IntentUnsupportedError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.INTENT_UNSUPPORTED, message, detail=detail)


class ContractViolationError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.CONTRACT_VIOLATION, message, detail=detail)


class EngineFailureError(ConversionError):
    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(ErrorCategory.ENGINE_FAILURE, message, detail=detail)
