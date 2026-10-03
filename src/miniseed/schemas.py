"""Pydantic request/response schemas for the verification HTTP API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IndexRequest(BaseModel):
    run_id: str = Field(..., min_length=1, description="unique run identity")
    reference: str = Field(..., min_length=1, description="reference DNA string")
    ref_name: str = "reference"
    k: int | None = Field(default=None, ge=1, le=31)
    w: int | None = Field(default=None, ge=1)
    overwrite: bool = False


class CandidateLocationOut(BaseModel):
    ref_start: int
    ref_end: int
    diagonal: int
    strand: str
    strand_consistent: bool
    hit_count: int
    hashes: list[int]


class IndexResponse(BaseModel):
    ok: Literal[True] = True
    request_id: str
    run_id: str
    k: int
    w: int
    hash_version: str
    ref_name: str
    ref_length: int
    seed_count: int
    distinct_seed_values: int
    max_bucket_size: int
    windows: int
    windows_without_kmer: int


class QueryRequest(BaseModel):
    run_id: str = Field(..., min_length=1)
    read: str = Field(..., min_length=1, description="synthetic query read")
    k: int | None = Field(default=None, ge=1, le=31)
    w: int | None = Field(default=None, ge=1)


class QueryResponse(BaseModel):
    ok: Literal[True] = True
    request_id: str
    run_id: str
    k: int
    w: int
    hash_version: str
    query_length: int
    query_seed_count: int
    windows: int
    windows_without_kmer: int
    total_hits: int
    candidate_is_alignment: bool
    note: str
    locations: list[CandidateLocationOut]


class RunSummary(BaseModel):
    run_id: str
    k: int
    w: int
    hash_version: str
    ref_name: str
    ref_length: int
    seed_count: int
    created_at: str


class ErrorBody(BaseModel):
    ok: Literal[False] = False
    request_id: str
    error: dict
