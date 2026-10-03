"""Typed error taxonomy.

Every failure raised by the domain layer carries a stable ``category`` string
so API responses, logs and tests can distinguish failure classes instead of
matching on message text.
"""
from __future__ import annotations

from typing import Any


class MotifScanError(Exception):
    """Base class for all declared, expected failures."""

    category = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


class ValidationError(MotifScanError):
    """Base class for input validation failures."""

    category = "validation_error"


class InvalidCharacterError(ValidationError):
    category = "invalid_character"


class EmptySequenceError(ValidationError):
    category = "empty_sequence"


class FastaFormatError(ValidationError):
    category = "fasta_format_error"


class DuplicateSequenceIdError(ValidationError):
    category = "duplicate_sequence_id"


class BackgroundNotNormalizedError(ValidationError):
    category = "background_not_normalized"


class ZeroBackgroundProbabilityError(ValidationError):
    category = "zero_background_probability"


class InvalidPseudocountError(ValidationError):
    category = "invalid_pseudocount"


class InvalidMotifMatrixError(ValidationError):
    category = "invalid_motif_matrix"


class MotifTooLongError(ValidationError):
    category = "motif_too_long_for_exact_calibration"


class UnknownBasePolicyError(ValidationError):
    category = "unknown_base_policy"


class InvalidAlphaError(ValidationError):
    category = "invalid_alpha"
