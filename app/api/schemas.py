"""Pydantic API schemas (validation boundary)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DigestRequest(BaseModel):
    # No min_length constraints: empty/blank values are categorized by the
    # service (EMPTY_SEQUENCE) rather than by a generic schema error.
    sequence: str = Field(
        ...,
        description="Synthetic protein sequence using A-Z residue tokens.",
    )
    enzyme: str = Field(
        ..., description="Enzyme name from the local catalog."
    )
    missed_cleavages: int = Field(
        0,
        description=(
            "Allowed missed cleavages (0 = complete digest). Range is "
            "validated by the service and returns INVALID_MISSED_CLEAVAGE."
        ),
    )
    run_id: str | None = Field(
        None,
        description="Optional caller-supplied run identity for log correlation.",
    )


class ErrorBody(BaseModel):
    success: bool = False
    error: dict[str, Any]


class RunSummary(BaseModel):
    run_id: str
    created_at: str
    status: str
    enzyme: str | None
    missed_cleavages: int | None
    sequence_length: int | None
    error_code: str | None
