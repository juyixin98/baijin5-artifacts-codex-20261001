"""Pydantic schemas for the validation API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    fasta: str = Field(min_length=1, description="Aligned sequences in FASTA format")
    label: str = Field(default="adhoc", max_length=200)


class ErrorBody(BaseModel):
    category: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ColumnOut(BaseModel):
    column_index: int
    distribution: dict[str, float]
    entropy_bits: float
    information_content_bits: float
    effective_coverage: float
    gap_fraction: float
    consensus: str
    status: str


class RunSummary(BaseModel):
    run_id: str
    label: str
    status: str
    created_at: str
    finished_at: str | None
    input_sha256: str
    n_sequences: int | None
    n_columns: int | None
    total_weight: float | None
    config: dict
    versions: dict
    error_category: str | None
    error_message: str | None


class RunDetail(BaseModel):
    run: RunSummary
    weights: list[dict]
    columns: list[ColumnOut]
    coordinate_maps: dict[str, list[int | None]]


class HealthResponse(BaseModel):
    status: str
    versions: dict
