"""Sample contracts: request validation, failure categories, decision vocabulary.

This module is framework-free. The FastAPI layer (api.py) translates
ContractViolation into HTTP responses; tests exercise it directly.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

# --- Supported operating range (declared, not implied) -----------------------
# Time scales outside this interval are rejected, not attempted.
MIN_TIME_SCALE = 0.5
MAX_TIME_SCALE = 2.0
MIN_SAMPLE_RATE = 8_000
MAX_SAMPLE_RATE = 48_000


class FailureCategory(str, Enum):
    """Machine-readable rejection reasons. Every rejection carries exactly one."""

    EMPTY_INPUT = "empty_input"
    MALFORMED_PAYLOAD = "malformed_payload"
    NON_FINITE_SAMPLES = "non_finite_samples"
    INVALID_SAMPLE_RATE = "invalid_sample_rate"
    UNSUPPORTED_TIME_SCALE = "unsupported_time_scale"
    INPUT_TOO_SHORT = "input_too_short"


class Decision(str, Enum):
    """Diagnostic verdict for a processed (or refused) request."""

    ACCEPTED = "accepted"
    ACCEPTED_WITH_NOTES = "accepted_with_notes"
    REJECTED = "rejected"
    # Correlation search carried no signal-derived information (e.g. pure
    # silence); output was still emitted using the deterministic default
    # (natural hop, offset 0), but the offsets must not be read as matches.
    UNDECIDABLE = "undecidable"


class ContractViolation(Exception):
    def __init__(self, category: FailureCategory, message: str):
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass(frozen=True)
class ValidatedRequest:
    sample_rate: int
    time_scale: float
    n_samples: int


def validate_request(
    sample_rate: int,
    time_scale: float,
    samples: np.ndarray,
    min_window_len: int,
) -> ValidatedRequest:
    """Validate a stretch request against the sample contract.

    Raises ContractViolation with a specific FailureCategory. Checks run in a
    fixed order so the reported category is deterministic for inputs that
    violate several rules at once.
    """
    if not isinstance(sample_rate, int) or not (MIN_SAMPLE_RATE <= sample_rate <= MAX_SAMPLE_RATE):
        raise ContractViolation(
            FailureCategory.INVALID_SAMPLE_RATE,
            f"sample_rate must be an int in [{MIN_SAMPLE_RATE}, {MAX_SAMPLE_RATE}], got {sample_rate!r}",
        )
    if not isinstance(time_scale, (int, float)) or not math.isfinite(time_scale):
        raise ContractViolation(
            FailureCategory.UNSUPPORTED_TIME_SCALE,
            f"time_scale must be finite, got {time_scale!r}",
        )
    if not (MIN_TIME_SCALE <= float(time_scale) <= MAX_TIME_SCALE):
        raise ContractViolation(
            FailureCategory.UNSUPPORTED_TIME_SCALE,
            f"time_scale {time_scale} outside supported range "
            f"[{MIN_TIME_SCALE}, {MAX_TIME_SCALE}]",
        )
    if samples.ndim != 1 or samples.shape[0] == 0:
        raise ContractViolation(FailureCategory.EMPTY_INPUT, "samples must be a non-empty 1-D array")
    if not np.all(np.isfinite(samples)):
        raise ContractViolation(
            FailureCategory.NON_FINITE_SAMPLES,
            "samples contain NaN or Inf",
        )
    if samples.shape[0] < min_window_len:
        raise ContractViolation(
            FailureCategory.INPUT_TOO_SHORT,
            f"need at least one window ({min_window_len} samples), got {samples.shape[0]}",
        )
    return ValidatedRequest(sample_rate=sample_rate, time_scale=float(time_scale), n_samples=int(samples.shape[0]))
