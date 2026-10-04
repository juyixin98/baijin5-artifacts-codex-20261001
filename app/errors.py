"""Typed error hierarchy.

Every failure the backend reports on purpose carries a stable machine-readable
``code`` so API clients and tests can assert on the failure *category*, not on
message text.
"""


class StftError(Exception):
    """Base class for all expected backend failures."""

    code = "STFT_ERROR"
    http_status = 400


class ConfigError(StftError):
    """A single parameter is out of range or unknown (e.g. bad window name)."""

    code = "INVALID_CONFIG"


class NotReconstructibleError(StftError):
    """The (window, hop, n_fft) combination cannot reconstruct the signal.

    Raised when the overlap-add normalisation denominator would be zero (or
    below tolerance) somewhere in the kept region, e.g. hop > win_length.
    """

    code = "NOT_RECONSTRUCTIBLE"


class ShapeMismatchError(StftError):
    """Spectrogram shape does not match the declared parameters."""

    code = "SHAPE_MISMATCH"


class EmptySignalError(StftError):
    """The input signal has no samples."""

    code = "EMPTY_SIGNAL"


class StreamStateError(StftError):
    """Illegal streaming state transition (e.g. push after flush)."""

    code = "STREAM_STATE"
    http_status = 409


class PayloadTooLargeError(StftError):
    """Request exceeds configured size limits."""

    code = "PAYLOAD_TOO_LARGE"
    http_status = 413
