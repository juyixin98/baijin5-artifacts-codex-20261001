"""HTTP request/response schemas (the external API contract)."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StrictInt

POSITION_DOC = "0-based position; a point denotes the single base at that index"
INTERVAL_DOC = "0-based, half-open interval [start, end): end is exclusive"


class PointRequest(BaseModel):
    transcript_id: Annotated[str, Field(min_length=1, examples=["T1_PLUS"])]
    position: Annotated[StrictInt, Field(ge=0, description=POSITION_DOC)]
    request_id: Annotated[
        str | None,
        Field(default=None, description="optional client correlation id"),
    ] = None


class IntervalRequest(BaseModel):
    transcript_id: Annotated[str, Field(min_length=1)]
    start: Annotated[StrictInt, Field(ge=0)]
    end: Annotated[StrictInt, Field(ge=1, description=INTERVAL_DOC)]
    request_id: Annotated[str | None, Field(default=None)] = None

    def model_post_init(self, __context: object) -> None:
        if self.start > self.end:
            raise ValueError("interval must satisfy start <= end (half-open [start, end))")


class FragmentOut(BaseModel):
    tx_start: int
    tx_end: int
    genomic_start: int
    genomic_end: int
    length: int
    exon_index: int
    genomic_order: int


class PointResult(BaseModel):
    transcript_id: str
    strand: str
    tx_position: int
    genomic_position: int
    exon_index: int


class IntervalResult(BaseModel):
    transcript_id: str
    strand: str
    tx_start: int
    tx_end: int
    length: int
    mapped_length: int
    fragment_count: int
    fragments: list[FragmentOut]


class MappingResponse(BaseModel):
    status: str
    request_id: str
    audit_id: str
    result: PointResult | IntervalResult


class TranscriptInfo(BaseModel):
    transcript_id: str
    chrom: str
    strand: str
    mature_length: int
    genomic_span: list[int] = Field(min_length=2, max_length=2)
    exons: list[list[int]]


class ErrorEnvelope(BaseModel):
    error: dict[str, object]
