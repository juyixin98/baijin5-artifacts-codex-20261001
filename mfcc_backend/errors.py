"""Typed error hierarchy for the MFCC backend.

Every failure raised by the library carries a stable ``category`` string so
callers (API layer, tests, demos) can assert on the *kind* of failure instead
of matching message text.  The API layer maps each category to an HTTP status
and never converts an unknown exception into a success response.
"""

from __future__ import annotations


class MFCCError(Exception):
    """Base class for all errors raised by mfcc_backend."""

    category = "internal"

    def __init__(self, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigError(MFCCError):
    """Invalid MFCC configuration (bad sample rate, n_fft < frame, ...)."""

    category = "config"


class InputContractError(MFCCError):
    """Input samples violate the sample contract (empty, NaN/inf, too long)."""

    category = "input_contract"


class EmptyFilterError(MFCCError):
    """One or more Mel filters have zero support on the FFT frequency axis."""

    category = "filterbank_empty_support"


class StreamStateError(MFCCError):
    """Illegal streaming state transition (chunk after finalize, ...)."""

    category = "stream_state"
