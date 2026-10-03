"""Pydantic request/response schemas for the HTTP boundary.

Structural validation only — all domain validation (background model,
motif matrix, alpha range, policies) lives in the domain layer and raises
typed MotifScanError failures with stable categories.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SequenceInput(BaseModel):
    id: str
    sequence: str


class MotifInput(BaseModel):
    name: str = "motif"
    # rows = motif positions, columns = counts for A, C, G, T
    matrix: list[list[float]]


class ModelInput(BaseModel):
    motif: MotifInput
    background: dict[str, float] | None = None
    pseudocount: float | None = None


class ScanRequest(ModelInput):
    sequences: list[SequenceInput]
    alpha: float = 0.05
    unknown_base_policy: Literal["skip", "marginalize"] = "skip"


class CalibrateRequest(ModelInput):
    alpha: float = 0.05
    include_distribution: bool = False


class ValidateRequest(BaseModel):
    motif: MotifInput | None = None
    background: dict[str, float] | None = None
    pseudocount: float | None = None
    alpha: float | None = None
    sequences: list[SequenceInput] | None = None
    unknown_base_policy: str | None = None


class Hit(BaseModel):
    hit_id: str
    seq_id: str
    start: int
    end: int
    strand: str
    matched_sequence: str
    score: float
    pvalue: float
    pvalue_bonferroni: float
    pvalue_bh: float
    significant_raw: bool
    significant_adjusted: bool  # BH-adjusted


class SkippedWindow(BaseModel):
    seq_id: str
    start: int
    end: int
    strand: str
    reason: str


class ThresholdInfo(BaseModel):
    requested_alpha: float
    achievable: bool
    score: float | None
    achieved_alpha: float


class ScanResponse(BaseModel):
    request_id: str
    app_version: str
    versions: dict[str, str]
    resolved_config: dict[str, Any]
    input_sha256: str
    threshold: ThresholdInfo
    n_windows_total: int
    n_scored_windows: int  # = number of tests in the correction family
    n_skipped_windows: int
    hits: list[Hit]
    uncertain_hit_ids: list[str]  # raw-significant but not BH-significant
    skipped_windows: list[SkippedWindow]
    caveats: list[str]


class DistributionEntry(BaseModel):
    score: float
    tail_probability: float


class CalibrateResponse(BaseModel):
    request_id: str
    app_version: str
    resolved_config: dict[str, Any]
    input_sha256: str
    motif_length: int
    n_distinct_scores: int
    min_score: float
    max_score: float
    threshold: ThresholdInfo
    distribution: list[DistributionEntry] | None = None


class ValidationIssue(BaseModel):
    category: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidateResponse(BaseModel):
    request_id: str
    valid: bool
    errors: list[ValidationIssue]


class ErrorBody(BaseModel):
    category: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    request_id: str
    error: ErrorBody
