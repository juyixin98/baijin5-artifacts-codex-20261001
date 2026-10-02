"""Data contract: request options, diagnostics and result envelopes.

The image payload itself is a 2-D single-channel array (grayscale or
binary).  Binary images are treated as the degenerate grayscale case
(values in {0, 1} or {0, 255}); the same kernel handles both.
"""

from __future__ import annotations

import enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Connectivity(enum.IntEnum):
    """Fixed neighborhood rule: 4- or 8-connected, no wrap-around."""

    FOUR = 4
    EIGHT = 8


class Engine(str, enum.Enum):
    """Available numerical kernels; all converge to the same fixpoint."""

    QUEUE = "queue"  # FIFO queue propagation (Vincent)
    REFERENCE = "reference"  # naive synchronous iteration
    TILED = "tiled"  # tile-scheduled sweeps with halo


class ViolationPolicy(str, enum.Enum):
    """What to do when the marker is not everywhere <= mask."""

    REJECT = "reject"  # refuse the request (HTTP 422)
    CLIP = "clip"  # explicitly clip marker to min(marker, mask)


class JobStatus(str, enum.Enum):
    ACCEPTED = "accepted"  # inputs valid, reconstruction computed
    CLIPPED = "clipped"  # marker violated mask and was clipped
    REJECTED = "rejected"  # inputs invalid; see reasons
    UNDETERMINED = "undetermined"  # could not decide (e.g. decode failure)


class FailureCategory(str, enum.Enum):
    DECODE_ERROR = "decode_error"
    NOT_GRAYSCALE = "not_grayscale"
    SHAPE_MISMATCH = "shape_mismatch"
    IMAGE_TOO_LARGE = "image_too_large"
    MARKER_EXCEEDS_MASK = "marker_exceeds_mask"
    EMPTY_MARKER = "empty_marker"
    BAD_OPTION = "bad_option"
    ITERATION_LIMIT = "iteration_limit"


class Diagnostics(BaseModel):
    """Structured, desensitized record of why a request was
    accepted / rejected / clipped / left undetermined.

    Never contains pixel data: only shapes, dtypes, counters and short
    content-hash prefixes so requests can be correlated without leaking
    image content into logs.
    """

    request_id: str
    status: JobStatus
    reasons: list[str] = Field(default_factory=list)
    failure_category: FailureCategory | None = None
    shape: list[int] | None = None
    dtype: str | None = None
    connectivity: int | None = None
    engine: str | None = None
    on_violation: str | None = None
    # Number of mask violations found before any clipping.
    violation_pixels: int = 0
    # Convergence record: synchronous sweeps (reference/tiled) or
    # queue pops (queue engine), plus pixels changed in the last pass.
    iterations: int | None = None
    queue_pops: int | None = None
    changed_pixels: int | None = None
    marker_sha256_12: str | None = None
    mask_sha256_12: str | None = None


class ReconstructionResult(BaseModel):
    """Success envelope for JSON responses."""

    diagnostics: Diagnostics
    result_png_b64: str
    result_shape: list[int]
    result_dtype: str


class ErrorResponse(BaseModel):
    """Failure envelope; mirrors Diagnostics so clients can branch on it."""

    diagnostics: Diagnostics
    detail: str


class ValidationReport(BaseModel):
    """Response of the /validate endpoint (no reconstruction performed)."""

    diagnostics: Diagnostics
    marker_stats: dict[str, Any] = Field(default_factory=dict)
    mask_stats: dict[str, Any] = Field(default_factory=dict)
