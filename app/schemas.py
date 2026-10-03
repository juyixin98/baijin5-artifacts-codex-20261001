"""Pydantic request/response contracts for the HTTP API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.dsp.cascade import InitialCondition, TransientStrategy
from app.errors import ErrorCategory


class CreateStreamRequest(BaseModel):
    sample_rate: float = Field(gt=0)
    num_channels: int = Field(ge=1)
    coefficients: list[list[float]] = Field(
        description="SOS rows [b0,b1,b2,a0,a1,a2]", min_length=1
    )
    initial_condition: InitialCondition = InitialCondition.ZERO


class StreamInfoResponse(BaseModel):
    stream_id: str
    sample_rate: float
    num_channels: int
    num_sections: int
    param_version: int
    max_pole_radius: float
    samples_processed: int
    blocks_processed: int


class ProcessBlockRequest(BaseModel):
    # samples[i][c]: sample i of channel c
    samples: list[list[float]] = Field(min_length=1)
    param_version: int | None = Field(
        default=None,
        description="If set, the block is rejected with 409 unless the "
        "stream is at this parameter version.",
    )


class ProcessBlockResponse(BaseModel):
    stream_id: str
    param_version: int
    samples: list[list[float]]
    samples_processed: int


class UpdateCoefficientsRequest(BaseModel):
    coefficients: list[list[float]] = Field(min_length=1)
    expected_version: int = Field(
        description="Optimistic-concurrency token: must equal the stream's "
        "current param_version or the update is rejected with 409."
    )
    transient: TransientStrategy = TransientStrategy.PRESERVE_STATE


class UpdateCoefficientsResponse(BaseModel):
    stream_id: str
    param_version: int
    num_sections: int
    max_pole_radius: float


class ErrorBody(BaseModel):
    category: ErrorCategory
    code: str
    message: str
    run_id: str
    context: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody
