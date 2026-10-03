"""Request/response contracts (Pydantic).

Syntactic validation lives here; numeric-domain validation (learning-rate
bounds, epsilon) lives in app.algorithms.lms.validate_spec so the same rules
apply to non-HTTP callers. Both surface as kind=input_validation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CreateStreamRequest(BaseModel):
    stream_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    algorithm: Literal["lms", "nlms"]
    filter_length: int = Field(ge=1)
    mu: float = Field(gt=0.0)
    epsilon: float | None = None
    channels: list[str] = Field(min_length=1)
    frozen_until_index: int = Field(default=0, ge=0)

    @field_validator("channels")
    @classmethod
    def _channel_ids_well_formed(cls, value: list[str]) -> list[str]:
        for ch in value:
            if not ch or len(ch) > 64:
                raise ValueError("channel ids must be 1..64 characters")
        return value


class CreateStreamResponse(BaseModel):
    stream_id: str
    run_id: str


class BlockRequest(BaseModel):
    start_index: int = Field(ge=0)
    reference: list[float] = Field(min_length=1)
    desired: list[float] = Field(min_length=1)
    freeze_adaptation: bool = False


class BlockResponse(BaseModel):
    run_id: str
    stream_id: str
    channel_id: str
    start_index: int
    end_index: int
    samples_processed: int
    frozen_samples: int
    weight_norm: float
    outputs: list[float]
    errors: list[float]


class StateResponse(BaseModel):
    stream_id: str
    channel_id: str
    algorithm: str
    filter_length: int
    mu: float
    epsilon: float
    frozen_until_index: int
    next_index: int
    weights: list[float]
    buffer: list[float]
    weight_norm: float


class EvaluateRequest(BaseModel):
    desired: list[float] = Field(min_length=1)
    residual: list[float] = Field(min_length=1)
    clean: list[float] = Field(min_length=1)
    plant_weights: list[float] | None = None
    estimated_weights: list[float] | None = None
    success_threshold_db: float = 6.0


class EvaluateResponse(BaseModel):
    run_id: str
    residual_mse_vs_clean: float
    noise_reduction_db: float
    coefficient_error_db: float | None
    success: bool
    rationale: str
