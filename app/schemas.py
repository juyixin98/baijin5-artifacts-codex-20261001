"""Request/response schemas.  Big integers travel as decimal strings so
JSON clients (including browsers) never lose precision."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateBatchRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    key_size: int | None = None
    max_plaintext_abs: int | None = None
    max_coefficient_abs: int | None = None
    max_aggregate_abs: int | None = None


class SubmitContributionRequest(BaseModel):
    participant_id: str = Field(min_length=1, max_length=200)
    key_id: str = Field(min_length=1)
    ciphertext: str = Field(description="decimal string of the ciphertext integer")
    coefficient: int
    plaintext_fixture: int | None = Field(
        default=None,
        description="TEST-ONLY: plaintext attached so the independent "
        "verifier can recompute the reference sum",
    )


class ErrorResponse(BaseModel):
    category: str
    message: str
    detail: dict
