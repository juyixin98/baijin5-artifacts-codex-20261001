"""Pydantic request/response schemas for the verification HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .coverage import UNION_PER_QUERY


class ReferenceIn(BaseModel):
    name: str = Field(min_length=1)
    length: int = Field(gt=0, le=10**9)


class AlignmentIn(BaseModel):
    query_name: str = Field(min_length=1)
    ref_name: str = Field(min_length=1)
    ref_start: int = Field(ge=0)
    cigar: str = Field(min_length=1, description="CIGAR, validity is adjudicated")
    mapq: int = Field(default=60, ge=0, le=255)
    query_length: int | None = Field(default=None, ge=0)
    strand: Literal["+", "-"] = "+"
    read_group: str = "default"
    is_duplicate: bool = False


class AnalysisOptions(BaseModel):
    min_mapq: int = Field(default=20, ge=0, le=255)
    dedup_policy: Literal["union_per_query", "per_record"] = UNION_PER_QUERY
    reject_flagged_duplicates: bool = True
    use_external_sort: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class AnalyzeRequest(BaseModel):
    references: list[ReferenceIn] = Field(min_length=1)
    alignments: list[AlignmentIn] = Field(default_factory=list)
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class AnalyzeTsvRequest(BaseModel):
    references: list[ReferenceIn] = Field(min_length=1)
    tsv: str = Field(description="Raw TSV records; malformed rows are undetermined")
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class SegmentOut(BaseModel):
    start: int
    end: int
    depth: int


class ReferenceResultOut(BaseModel):
    ref_name: str
    ref_length: int
    per_base_depth: list[int]
    segments: list[SegmentOut]
    histogram: dict[int, int]
    weighted_length: int
    covered_bases: int
    accepted_queries: list[str]


class CountSummary(BaseModel):
    input: int
    accepted: int
    rejected: int
    undetermined: int


class AnalyzeResponse(BaseModel):
    request_id: str
    run_id: str
    counts: CountSummary
    results: dict[str, ReferenceResultOut]
    conservation: dict[str, dict[str, int]]
    external_sorted: bool
    diagnostics: list[dict]
