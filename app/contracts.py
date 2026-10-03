"""Sample contracts: request/response schemas for the LPC backend.

These models are the single source of truth for what crosses the API
boundary. Every response carries the request identity, the component
version and the processing location so results stay interpretable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import MAX_FRAME_SIZE, MAX_ORDER, MAX_SAMPLES


class LPCConfigModel(BaseModel):
    """Fixed analysis configuration shared by all frames of a request."""

    frame_size: int = Field(default=256, ge=8, le=MAX_FRAME_SIZE)
    order: int = Field(default=10, ge=1, le=MAX_ORDER)
    window: Literal["hann", "hamming", "rect"] = "hann"

    @field_validator("order")
    @classmethod
    def order_below_frame_size(cls, v: int, info) -> int:
        frame_size = info.data.get("frame_size")
        if frame_size is not None and v >= frame_size:
            raise ValueError(
                f"order ({v}) must be smaller than frame_size ({frame_size}); "
                "an order at or above the frame length makes the "
                "autocorrelation matrix structurally singular"
            )
        return v


class AnalyzeRequest(BaseModel):
    samples: list[float] = Field(min_length=1, max_length=MAX_SAMPLES)
    config: LPCConfigModel = LPCConfigModel()

    @field_validator("samples")
    @classmethod
    def samples_must_be_finite(cls, v: list[float]) -> list[float]:
        import math

        if any(not math.isfinite(x) for x in v):
            raise ValueError("samples must be finite numbers")
        return v


class StabilityReport(BaseModel):
    stable: bool
    max_abs_reflection: float
    diagnostics: list[str] = []


class FrameResult(BaseModel):
    frame_index: int
    offset: int
    lpc: list[float]
    reflection_coeffs: list[float]
    gain: float
    residual: list[float]
    zero_energy: bool
    stability: StabilityReport


class ProcessingInfo(BaseModel):
    version: str
    module: str
    config: LPCConfigModel
    n_samples: int
    n_frames: int


class AnalyzeResponse(BaseModel):
    request_id: str
    processing: ProcessingInfo
    frames: list[FrameResult]
    diagnostics: list[str] = []
    uncertain: list[str] = []


class EncodedFrame(BaseModel):
    """One frame as produced by /analyze and consumed by /reconstruct."""

    lpc: list[float] = Field(min_length=2)
    residual: list[float] = Field(min_length=1)
    zero_energy: bool = False


class ReconstructRequest(BaseModel):
    frames: list[EncodedFrame] = Field(min_length=1)
    config: LPCConfigModel = LPCConfigModel()


class ReconstructResponse(BaseModel):
    request_id: str
    processing: ProcessingInfo
    samples: list[float]
    diagnostics: list[str] = []


class RoundtripResponse(BaseModel):
    request_id: str
    processing: ProcessingInfo
    metrics: dict
    lossless_confirmed: bool
    verdict: str
    reasons: list[str] = []
    diagnostics: list[str] = []
    uncertain: list[str] = []


class ErrorBody(BaseModel):
    category: str
    message: str


class ErrorResponse(BaseModel):
    request_id: str
    version: str
    error: ErrorBody
