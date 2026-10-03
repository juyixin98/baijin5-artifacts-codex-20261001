"""Domain error taxonomy.

Framework-free: every error carries an HTTP-ish ``status_code`` and a stable
machine-readable ``code`` so the HTTP layer can map it without the DSP or
stream layers importing anything web-specific.  The ``code`` values are the
failure categories asserted by the tests and reported in diagnostics.
"""
from __future__ import annotations


class DomainError(Exception):
    status_code: int = 400
    code: str = "DOMAIN_ERROR"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class SessionNotFoundError(DomainError):
    status_code = 404
    code = "SESSION_NOT_FOUND"


class SessionLimitReachedError(DomainError):
    status_code = 429
    code = "SESSION_LIMIT_REACHED"


class BlockSizeMismatchError(DomainError):
    status_code = 409
    code = "BLOCK_SIZE_MISMATCH"


class EmptyBlockError(DomainError):
    status_code = 422
    code = "EMPTY_BLOCK"


class InputAfterFinalBlockError(DomainError):
    status_code = 409
    code = "INPUT_AFTER_FINAL_BLOCK"


class SessionAlreadyFlushedError(DomainError):
    status_code = 409
    code = "SESSION_ALREADY_FLUSHED"


class StateBudgetExceededError(DomainError):
    status_code = 413
    code = "STATE_BUDGET_EXCEEDED"


class InvalidBlockSizeError(DomainError):
    status_code = 422
    code = "INVALID_BLOCK_SIZE"


class InvalidIRError(DomainError):
    status_code = 422
    code = "INVALID_IR"


class InvalidSamplesError(DomainError):
    status_code = 422
    code = "INVALID_SAMPLES"
