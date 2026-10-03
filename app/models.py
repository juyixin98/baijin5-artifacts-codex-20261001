"""Request/response schemas for the phasing API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VariantIn(BaseModel):
    id: str = Field(min_length=1)
    chrom: str = "chrSynth"
    pos: int = Field(ge=1)
    ref: str = Field(min_length=1)
    alt: str = Field(min_length=1)


class ReadObservationIn(BaseModel):
    read_id: str = Field(min_length=1)
    variant_id: str = Field(min_length=1)
    allele: str = Field(min_length=1)
    quality: int | None = Field(default=None, ge=0)


class PhaseRequest(BaseModel):
    sample: str = "synthetic"
    variants: list[VariantIn] = Field(min_length=1)
    reads: list[ReadObservationIn] = Field(default_factory=list)
