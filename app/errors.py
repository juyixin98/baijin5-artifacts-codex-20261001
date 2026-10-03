"""Domain error types with stable, machine-readable failure categories.

Every failure surfaced by the API carries one of these categories so that
clients and log readers can distinguish *why* something failed without
parsing free-text messages.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(str, Enum):
    # Request / input validation failures (HTTP 422).
    INVALID_SEQUENCE = "INVALID_SEQUENCE"
    INVALID_MOTIF = "INVALID_MOTIF"
    INVALID_PSEUDOCOUNT = "INVALID_PSEUDOCOUNT"
    INVALID_BACKGROUND = "INVALID_BACKGROUND"
    ZERO_BACKGROUND_PROBABILITY = "ZERO_BACKGROUND_PROBABILITY"
    BACKGROUND_NOT_NORMALIZED = "BACKGROUND_NOT_NORMALIZED"
    MOTIF_TOO_LONG = "MOTIF_TOO_LONG"
    INVALID_THRESHOLD = "INVALID_THRESHOLD"
    UNKNOWN_POLICY_INVALID = "UNKNOWN_POLICY_INVALID"
    # Schema-level request validation failures (HTTP 422).
    INVALID_REQUEST = "INVALID_REQUEST"
    # Lookup failures (HTTP 404).
    SCAN_NOT_FOUND = "SCAN_NOT_FOUND"


class DomainError(Exception):
    """An expected, categorised failure in the domain layer."""

    def __init__(self, category: ErrorCategory, message: str, detail: dict | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.detail = detail or {}

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "detail": self.detail,
        }
