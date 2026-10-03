"""Domain error hierarchy.

Every failure raised by the library carries a stable ``category`` string so
the API layer and tests can assert on the *kind* of failure, not just that
"an exception happened". Unknown states must never be collapsed into a
success response.
"""

from __future__ import annotations


class MinimizerIndexError(Exception):
    """Base class for all domain errors raised by this package."""

    category = "INTERNAL_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class InvalidParameterError(MinimizerIndexError):
    """Incompatible or out-of-range parameters (k, w, caps, hash name)."""

    category = "INVALID_PARAMETER"


class InvalidSequenceError(MinimizerIndexError):
    """Sequence contains non-ACGT characters or is empty."""

    category = "INVALID_SEQUENCE"


class SequenceTooShortError(MinimizerIndexError):
    """Sequence is shorter than k + w - 1, so no full window exists."""

    category = "SEQUENCE_TOO_SHORT"


class IndexNotFoundError(MinimizerIndexError):
    """Requested index id does not exist on disk."""

    category = "INDEX_NOT_FOUND"


class IndexStateError(MinimizerIndexError):
    """Index file exists but is corrupt or built with incompatible params."""

    category = "INDEX_STATE_ERROR"
