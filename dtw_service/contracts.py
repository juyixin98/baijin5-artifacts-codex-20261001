"""Sample contracts: request/response schemas, statuses, failure categories.

These models are the stable boundary between the HTTP surface, the streaming
session layer, and the numerical core. Every alignment attempt resolves to
exactly one ``DecisionStatus``; failures always carry a ``FailureCategory``
so callers can branch on the failure class instead of parsing messages.
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DecisionStatus(str, enum.Enum):
    """Outcome of an alignment attempt."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    # Alignment was computed but is not uniquely identifiable from the data
    # (e.g. all local distances in the band are zero, so every legal path is
    # equally optimal). A path is still returned, flagged as non-unique.
    INDETERMINATE = "indeterminate"


class FailureCategory(str, enum.Enum):
    """Machine-readable failure classes for rejected alignments."""

    EMPTY_SEQUENCE = "empty_sequence"
    NON_FINITE_VALUES = "non_finite_values"
    LENGTH_LIMIT_EXCEEDED = "length_limit_exceeded"
    INVALID_WINDOW = "invalid_window"
    WINDOW_TOO_NARROW = "window_too_narrow"
    UNREACHABLE_ENDPOINT = "unreachable_endpoint"


class AlignmentRequest(BaseModel):
    """One DTW alignment request over two scalar feature sequences."""

    model_config = ConfigDict(extra="forbid")

    sequence_a: list[float] = Field(..., description="First feature sequence.")
    sequence_b: list[float] = Field(..., description="Second feature sequence.")
    window_radius: int | None = Field(
        default=None,
        description="Sakoe-Chiba radius; falls back to service settings when omitted.",
    )
    record_id: str | None = Field(
        default=None,
        description="Caller-supplied record id echoed into diagnostics.",
    )


class FailureInfo(BaseModel):
    category: FailureCategory
    message: str


class AlignmentResponse(BaseModel):
    """Result of one alignment attempt.

    ``path`` is a list of ``[i, j]`` index pairs, monotone under the fixed
    step pattern. ``cost`` is the accumulated cost; ``normalized_cost`` is
    ``cost / (len(a) + len(b))`` — the denominator is the number of consumed
    samples, identical for every legal path, so normalized costs are
    comparable across pairs. ``stretch`` holds the per-transition local
    stretch rate (samples of A consumed per sample of B).
    """

    request_id: str
    status: DecisionStatus
    failure: FailureInfo | None = None
    path: list[list[int]] | None = None
    cost: float | None = None
    normalized_cost: float | None = None
    stretch: list[float] | None = None
    stretch_smoothed: list[float] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
