"""Typed failure categories shared across parser, domain and API layers."""
from __future__ import annotations


class NussinovError(Exception):
    """Base class for all expected, explainable failures."""

    category = "internal_error"


class SequenceValidationError(NussinovError):
    """Base class for input validation failures."""

    category = "validation_error"


class EmptySequenceError(SequenceValidationError):
    category = "empty_sequence"


class SequenceTooLongError(SequenceValidationError):
    category = "sequence_too_long"

    def __init__(self, message: str, *, length: int, max_length: int) -> None:
        super().__init__(message)
        self.length = length
        self.max_length = max_length


class InvalidBaseError(SequenceValidationError):
    category = "invalid_base"

    def __init__(self, message: str, *, position: int | None, base: str | None) -> None:
        super().__init__(message)
        self.position = position
        self.base = base


class InvalidParameterError(SequenceValidationError):
    category = "invalid_parameter"

    def __init__(self, message: str, *, parameter: str, value: object) -> None:
        super().__init__(message)
        self.parameter = parameter
        self.value = value


class StructureError(NussinovError):
    """Raised when a produced/fetched structure violates model constraints."""

    category = "structure_error"


class TracebackError(NussinovError):
    """Raised when traceback cannot reproduce the DP optimum (integrity bug)."""

    category = "traceback_error"


class LineageError(NussinovError):
    category = "lineage_error"


class ResultNotFoundError(NussinovError):
    category = "result_not_found"
