"""Typed error categories shared by parsing, digestion, API and persistence.

Error *categories* are first-class values: callers (and tests) assert on the
stable ``code`` string. Unknown / failure states must never be flattened into a
success response.
"""

from __future__ import annotations


class DigestError(Exception):
    """Base class for expected, categorized service failures."""

    code: str = "DIGEST_ERROR"
    http_status: int = 400

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


class EmptySequenceError(DigestError):
    code = "EMPTY_SEQUENCE"
    http_status = 422


class SequenceTooLongError(DigestError):
    code = "SEQUENCE_TOO_LONG"
    http_status = 422


class IllegalSymbolError(DigestError):
    """Symbols that are not amino-acid characters at all (digits, spaces, ...)."""

    code = "ILLEGAL_SYMBOL"
    http_status = 422


class UnknownResidueError(DigestError):
    """Letters outside the supported residue alphabet (e.g. ``*``).

    Distinct from :class:`IllegalSymbolError`: the token is a plausible residue
    marker but absent from the local mass/rule table.
    """

    code = "UNKNOWN_RESIDUE"
    http_status = 422


class InvalidMissedCleavageError(DigestError):
    code = "INVALID_MISSED_CLEAVAGE"
    http_status = 422


class EnzymeNotFoundError(DigestError):
    code = "ENZYME_NOT_FOUND"
    http_status = 404


class InvalidRuleError(DigestError):
    code = "INVALID_RULE"
    http_status = 422


class RunNotFoundError(DigestError):
    code = "RUN_NOT_FOUND"
    http_status = 404


class PersistenceError(DigestError):
    code = "PERSISTENCE_ERROR"
    http_status = 500
