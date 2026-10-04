"""Protocol failure categories.

Every rejection in the protocol maps to one of these codes so that callers,
audit records, and tests can assert on a stable failure taxonomy instead of
matching free-text messages.
"""

from __future__ import annotations

import enum


class ErrorCode(str, enum.Enum):
    # Commit-phase failures
    ROUND_NOT_FOUND = "ROUND_NOT_FOUND"
    ROUND_NOT_OPEN = "ROUND_NOT_OPEN"
    LATE_COMMIT = "LATE_COMMIT"
    UNKNOWN_PARTICIPANT = "UNKNOWN_PARTICIPANT"
    DUPLICATE_COMMIT = "DUPLICATE_COMMIT"
    MALFORMED_COMMITMENT = "MALFORMED_COMMITMENT"

    # Reveal-phase failures
    REVEAL_BEFORE_FREEZE = "REVEAL_BEFORE_FREEZE"
    ROUND_FINALIZED = "ROUND_FINALIZED"
    LATE_REVEAL = "LATE_REVEAL"
    UNKNOWN_COMMITMENT = "UNKNOWN_COMMITMENT"
    DUPLICATE_REVEAL = "DUPLICATE_REVEAL"
    COMMITMENT_MISMATCH = "COMMITMENT_MISMATCH"
    MALFORMED_REVEAL = "MALFORMED_REVEAL"

    # Finalization failures
    ROUND_NOT_FINALIZABLE = "ROUND_NOT_FINALIZABLE"
    NO_VALID_REVEALS = "NO_VALID_REVEALS"

    # Request-shape failures
    INVALID_ROUND_CONFIG = "INVALID_ROUND_CONFIG"


class ProtocolError(Exception):
    """A rejected protocol operation with a stable failure category."""

    def __init__(self, code: ErrorCode, reason: str):
        self.code = code
        self.reason = reason
        super().__init__(f"{code.value}: {reason}")
