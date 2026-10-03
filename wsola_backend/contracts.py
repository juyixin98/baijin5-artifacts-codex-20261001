"""Request/response sample contract (Pydantic schemas).

The contract is the stability boundary of the service: clients depend on
exact output length semantics and on the per-segment match offsets.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]


class TimeStretchRequest(BaseModel):
    """Time-stretch one mono float signal.

    samples: mono PCM as floats (any finite range; fixtures use [-1, 1]).
    rate:    speed factor, output_length = round(len(samples) / rate).
    request_id: client-supplied correlation id; generated when omitted.
    """

    model_config = ConfigDict(extra="forbid")

    sample_rate: int = Field(gt=0, le=384_000)
    rate: FiniteFloat
    samples: list[FiniteFloat] = Field(min_length=1, max_length=10_000_000)
    request_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class SegmentRecord(BaseModel):
    """One WSOLA segment decision.

    ideal_position:    k * rate * Hs (float, before rounding).
    nominal_position:  round(ideal_position); per-segment rounding keeps the
                       position drift <= 0.5 sample (no accumulation).
    delta:             chosen match offset in [-search_radius, +search_radius].
    analysis_position: nominal_position + delta (input index of the frame).
    correlation:       winning normalized cross-correlation; null for segment 0
                       (no overlap exists yet, delta is fixed to 0).
    degenerate:        True when the match was undecidable (silence/DC on either
                       side) and delta was chosen by the deterministic
                       tie-break rule instead of by similarity.
    pinned:            True when the ideal analysis trajectory ran past the
                       last placeable frame start (strong slow-down tail);
                       the segment was pinned to n - L without a search.
    """

    index: int
    output_position: int
    ideal_position: float
    nominal_position: int
    delta: int
    analysis_position: int
    correlation: Optional[float]
    degenerate: bool
    pinned: bool


class DiagnosticRecord(BaseModel):
    """A decision the service made about the request, with key state.

    level: info | warning. Rejections are returned as HTTP 422 whose detail
    body carries the same record shape plus the request_id.
    """

    level: Literal["info", "warning"]
    code: str
    message: str
    context: dict[str, object] = {}


class StretchStats(BaseModel):
    input_length: int
    target_length: int
    output_length: int
    frames_planned: int
    frames_placed: int
    end_rule: Literal["exact", "trim"]
    end_compensation_samples: int  # <= 0; |value| samples trimmed
    max_position_drift: float  # max |nominal - ideal|, always <= 0.5


class TimeStretchResponse(BaseModel):
    request_id: str
    sample_rate: int
    rate: float
    samples: list[float]
    segments: list[SegmentRecord]
    stats: StretchStats
    diagnostics: list[DiagnosticRecord]


class RejectionDetail(BaseModel):
    """Body of HTTP 422 responses for domain rejections."""

    request_id: str
    error: DiagnosticRecord
    diagnostics: list[DiagnosticRecord]
