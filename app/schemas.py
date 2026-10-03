"""Pydantic request/response schemas for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScanRequest(BaseModel):
    # Raw sequence or (multi-record) FASTA text.
    sequences: str = Field(..., min_length=1, description="Raw sequence or FASTA text")
    # Count matrix: rows = motif positions, columns = [A, C, G, T].
    motif_counts: list[list[float]] = Field(..., description="Count matrix, columns A,C,G,T")
    background: dict[str, float] | None = Field(
        default=None, description="Optional override of the configured background model"
    )
    pseudocount: float | None = Field(default=None, gt=0, description="Optional pseudocount override")
    # Exactly one threshold mode may be given; if neither is given the
    # configured default p-value threshold applies.
    score_threshold: float | None = None
    pvalue_threshold: float | None = Field(default=None, gt=0, le=1)
    unknown_policy: Literal["skip", "marginalize"] | None = None
    label: str | None = Field(default=None, max_length=200, description="Free-form caller label")


class HitOut(BaseModel):
    seq_id: str
    strand: str
    start: int
    end: int
    matched: str
    score: float
    pvalue: float
    bonferroni: float
    benjamini_hochberg: float


class ThresholdInfo(BaseModel):
    mode: str  # 'score' | 'pvalue' | 'default_pvalue'
    # None when the requested p-value is unreachable (THRESHOLD_UNREACHABLE).
    score_threshold: float | None
    requested_pvalue_threshold: float | None
    achieved_pvalue: float | None  # tail probability at the resolved score threshold


class ScanSummary(BaseModel):
    n_sequences: int
    motif_length: int
    evaluated_windows: int
    skipped_windows: int
    n_hits: int
    enumerated_words: int
    warnings: list[dict]


class ScanResponse(BaseModel):
    request_id: str
    status: str
    app_version: str
    algorithm_version: str
    request_hash: str
    config: dict
    threshold: ThresholdInfo
    summary: ScanSummary
    hits: list[HitOut]


class ErrorBody(BaseModel):
    category: str
    message: str
    detail: dict = {}


class ErrorResponse(BaseModel):
    request_id: str | None
    error: ErrorBody
