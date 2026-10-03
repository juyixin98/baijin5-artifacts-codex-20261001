"""Failure categories shared by parsing, service and API layers.

Every rejected dataset or aborted run maps to one of these categories so
that API consumers and tests can assert on the *kind* of failure, not just
on an HTTP status code.
"""

from __future__ import annotations

import enum


class FailureCategory(str, enum.Enum):
    # Dataset validation failures (HTTP 422).
    EMPTY_DATASET = "EMPTY_DATASET"
    DUPLICATE_SITE_ID = "DUPLICATE_SITE_ID"
    DUPLICATE_READ_ID = "DUPLICATE_READ_ID"
    DUPLICATE_CALL_IN_READ = "DUPLICATE_CALL_IN_READ"
    INVALID_REFERENCE_ALLELE = "INVALID_REFERENCE_ALLELE"
    INVALID_ALT_ALLELE = "INVALID_ALT_ALLELE"
    QUALITY_OUT_OF_RANGE = "QUALITY_OUT_OF_RANGE"
    READ_REFERENCES_UNKNOWN_SITE = "READ_REFERENCES_UNKNOWN_SITE"
    # Processing failures (HTTP 422: well-formed but not phasable as asked).
    BLOCK_TOO_LARGE = "BLOCK_TOO_LARGE"
    NO_INFORMATIVE_READS = "NO_INFORMATIVE_READS"
    # Infrastructure failures (HTTP 500).
    PROVENANCE_STORE_ERROR = "PROVENANCE_STORE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DatasetError(ValueError):
    """Raised when the input dataset violates a validation rule."""

    def __init__(self, category: FailureCategory, message: str, context: dict | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "context": self.context,
        }


class ProcessingError(RuntimeError):
    """Raised when a valid dataset cannot be phased under current config."""

    def __init__(self, category: FailureCategory, message: str, context: dict | None = None):
        super().__init__(message)
        self.category = category
        self.message = message
        self.context = context or {}

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "message": self.message,
            "context": self.context,
        }
