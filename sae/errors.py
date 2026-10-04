"""Error taxonomy for the segmented-AE service.

Every failure raised by the service layer carries a stable ``category``
string so that API responses, audit records and tests can assert on the
*kind* of failure instead of matching message text.

Categories
----------
incomplete_stream      one or more sequence numbers are missing, or the
                       terminating chunk was never received
final_flag_misplaced   a non-last chunk carries the final flag, or more
                       than one final chunk exists
tag_mismatch           AEAD verification failed for a stored chunk
                       (tampering, reordering across messages, ...)
nonce_reuse_conflict   a retry tried to encrypt *different* content under
                       a (message, seq) pair that already has a nonce in use
message_state_conflict the message is already released / failed / unknown
undecidable            the service cannot determine the outcome (e.g. an
                       interrupted operation) and the caller must retry
"""

from __future__ import annotations


class SAEError(Exception):
    """Base class for all service errors."""

    category = "internal_error"
    http_status = 500

    def __init__(self, reason: str, **context: object) -> None:
        super().__init__(reason)
        self.reason = reason
        # Context must only ever contain non-sensitive metadata
        # (ids, lengths, hashes, counters) - never plaintext or keys.
        self.context = context


class NotFound(SAEError):
    category = "not_found"
    http_status = 404


class IncompleteStream(SAEError):
    category = "incomplete_stream"
    http_status = 409


class FinalFlagMisplaced(SAEError):
    category = "final_flag_misplaced"
    http_status = 409


class TagMismatch(SAEError):
    category = "tag_mismatch"
    http_status = 422


class NonceReuseConflict(SAEError):
    category = "nonce_reuse_conflict"
    http_status = 409


class MessageStateConflict(SAEError):
    category = "message_state_conflict"
    http_status = 409


class Undecidable(SAEError):
    category = "undecidable"
    http_status = 503
