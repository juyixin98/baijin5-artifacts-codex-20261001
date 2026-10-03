"""Error taxonomy for the LMS/NLMS backend.

Four distinguishable categories. Each maps to a stable HTTP status and a
machine-readable ``category`` string in the API error envelope so callers
(and test logs) can tell apart:

- ``input_error``        (400): malformed or out-of-range request data
- ``state_conflict``     (409): request inconsistent with stored channel state
- ``resource_exhausted`` (429): configured capacity limits exceeded
- ``computation_failed`` (500): non-finite or inconsistent numerical state
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for all expected backend errors."""

    category: str = "internal"
    http_status: int = 500

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail or {}

    def to_envelope(self, run_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "category": self.category,
                "message": self.message,
                "detail": self.detail,
                "run_id": run_id,
            }
        }


class InputValidationError(AppError):
    category = "input_error"
    http_status = 400


class StateConflictError(AppError):
    category = "state_conflict"
    http_status = 409


class ResourceExhaustedError(AppError):
    category = "resource_exhausted"
    http_status = 429


class ComputationError(AppError):
    category = "computation_failed"
    http_status = 500
