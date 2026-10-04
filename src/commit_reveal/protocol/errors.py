"""Typed protocol errors.

Each error carries a stable ``category`` string. The API layer maps the
category to an HTTP status and echoes it in the response body so clients
(and tests) can assert on failure *category*, not on prose messages.
"""

from __future__ import annotations


class ProtocolError(Exception):
    category = "PROTOCOL_ERROR"
    http_status = 409

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context = context


class RoundNotFoundError(ProtocolError):
    category = "ROUND_NOT_FOUND"
    http_status = 404


class UnknownParticipantError(ProtocolError):
    category = "UNKNOWN_PARTICIPANT"
    http_status = 403


class LateCommitmentError(ProtocolError):
    category = "LATE_COMMITMENT"
    http_status = 409


class DuplicateCommitmentError(ProtocolError):
    category = "DUPLICATE_COMMITMENT"
    http_status = 409


class NoCommitmentError(ProtocolError):
    category = "NO_COMMITMENT"
    http_status = 409


class RevealPhaseNotOpenError(ProtocolError):
    category = "REVEAL_PHASE_NOT_OPEN"
    http_status = 409


class LateRevealError(ProtocolError):
    category = "LATE_REVEAL"
    http_status = 409


class CommitmentMismatchError(ProtocolError):
    category = "COMMITMENT_MISMATCH"
    http_status = 422


class DuplicateRevealError(ProtocolError):
    category = "DUPLICATE_REVEAL"
    http_status = 409


class RoundNotFinalizableError(ProtocolError):
    category = "ROUND_STATE"
    http_status = 409
