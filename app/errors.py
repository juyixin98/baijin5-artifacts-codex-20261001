"""Error taxonomy for the adaptive-filtering backend.

Every failure raised by the service layer is one of the four kinds below.
The HTTP layer maps them to status codes, and every error body carries the
machine-readable ``kind`` and ``reason`` so clients and test logs can
distinguish failure classes without parsing messages.

Kinds
-----
input_validation   -> 422  malformed or out-of-range request data
state_conflict     -> 409  request inconsistent with current stream state
                     (404 when the conflicting fact is "stream/channel absent")
resource_exhausted -> 413  configured capacity limits exceeded
computation_failure-> 500  non-finite or otherwise failed numerical work
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class carrying the error contract."""

    kind: str = "internal"
    http_status: int = 500

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        detail: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.detail = detail or {}
        if http_status is not None:
            self.http_status = http_status

    def to_body(self, run_id: str) -> dict[str, Any]:
        return {
            "error": {
                "kind": self.kind,
                "reason": self.reason,
                "message": self.message,
                "detail": self.detail,
                "run_id": run_id,
            }
        }


class InputValidationError(AppError):
    kind = "input_validation"
    http_status = 422


class StateConflictError(AppError):
    kind = "state_conflict"
    http_status = 409


class ResourceExhaustedError(AppError):
    kind = "resource_exhausted"
    http_status = 413


class ComputationError(AppError):
    kind = "computation_failure"
    http_status = 500
