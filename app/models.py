"""Request/response schemas (the sample and control contract of the API)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateStreamRequest(BaseModel):
    sos: list[list[float]] = Field(
        description="SOS cascade, one [b0, b1, b2, a0, a1, a2] row per section"
    )
    n_channels: int = Field(ge=1)
    sample_rate: float | None = Field(default=None, gt=0)
    transient: Literal["carry", "reset"] = "carry"


class StreamInfoResponse(BaseModel):
    stream_id: str
    n_channels: int
    n_sections: int
    param_version: int
    transient: str
    samples_processed: int
    chunks_processed: int
    max_pole_radius: float
    sample_rate: float | None


class CreateStreamResponse(BaseModel):
    stream_id: str
    param_version: int


class ChunkRequest(BaseModel):
    samples: list[list[float]] = Field(
        description="Shape (n_channels, n_samples); one inner list per channel"
    )
    expected_param_version: int | None = None


class ChunkResponse(BaseModel):
    samples: list[list[float]]
    param_version: int
    samples_processed: int
    chunks_processed: int


class UpdateCoefficientsRequest(BaseModel):
    sos: list[list[float]]
    transient: Literal["carry", "reset"] | None = None
    expected_param_version: int | None = None


class UpdateCoefficientsResponse(BaseModel):
    stream_id: str
    param_version: int
    transient: str
    max_pole_radius: float
