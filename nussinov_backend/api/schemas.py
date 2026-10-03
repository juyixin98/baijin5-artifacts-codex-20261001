"""Pydantic request/response schemas for the validation interface."""
from __future__ import annotations

from pydantic import BaseModel, Field


class FoldRequestBody(BaseModel):
    sequence: str = Field(
        ...,
        min_length=1,
        description="RNA sequence (ACGU; T accepted and converted to U). "
        "Plain string or FASTA-like text.",
    )
    enumerate_alternatives: bool = Field(
        default=False,
        description="If true, enumerate other structures tied with the optimum.",
    )
    alternatives_limit: int | None = Field(
        default=None,
        ge=1,
        description="Maximum number of optimal structures to return.",
    )


class PairEntry(BaseModel):
    position_5prime: int = Field(..., description="1-based position of the 5' base")
    position_3prime: int = Field(..., description="1-based position of the 3' base")
    base_5prime: str
    base_3prime: str


class StructureOut(BaseModel):
    rank: int
    is_primary: bool
    pair_count: int
    dot_bracket: str
    pairs: list[PairEntry]
    pair_table: list[int] = Field(
        ..., description="pair_table[i] is the 1-based partner of 1-based position i; 0 = unpaired"
    )


class ProcessingStepOut(BaseModel):
    order: int
    name: str
    outcome: str
    detail: str
    at: str


class ErrorOut(BaseModel):
    category: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ProvenanceOut(BaseModel):
    request_id: str
    algorithm_name: str
    algorithm_version: str
    model_scope: str
    processing_location: str
    created_at: str
    finished_at: str
    duration_ms: float
    source: str
    stored: bool = Field(..., description="whether this result is retrievable from lineage storage")


class FoldResponse(BaseModel):
    success: bool
    request_id: str
    sequence: str
    sequence_length: int
    optimum: int
    min_loop_length: int
    pseudoknots_supported: bool
    allowed_pairs: list[str]
    structure: StructureOut | None
    alternatives: list[StructureOut]
    alternatives_truncated: bool
    legal_structure: bool
    legality_violations: list[str]
    uncertainties: list[str] = Field(
        ..., description="model-level caveats; this teaching model makes no folding-reliability claim"
    )
    processing_steps: list[ProcessingStepOut]
    provenance: ProvenanceOut
    error: ErrorOut | None = None


class HealthResponse(BaseModel):
    status: str
    algorithm: str
    version: str
    model_scope: str
    db_path: str
