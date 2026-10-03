"""API sample contracts (Pydantic models).

Every response carries a ``meta`` block with the request identity, the
service/pipeline version and the pipeline stages that ran, plus separate
``errors`` (failure reasons) and ``warnings`` (uncertain conclusions)
lists distilled from the structured diagnostics.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

from app.config import Settings

WindowName = Literal["hann", "hamming", "rect"]


# ---------------------------------------------------------------- requests


class FrameParams(BaseModel):
    samples: list[float]
    order: int | None = None
    window: WindowName | None = None
    verify_toeplitz: bool = False

    @field_validator("samples")
    @classmethod
    def _samples_finite(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("samples must not be empty")
        if not all(math.isfinite(s) for s in v):
            raise ValueError("samples must be finite numbers (no NaN/inf)")
        return v

    @field_validator("order")
    @classmethod
    def _order_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("order must be >= 1")
        return v


class AnalyzeRequest(FrameParams):
    pass


class RoundtripRequest(FrameParams):
    pass


class SynthesizeRequest(BaseModel):
    coefficients: list[float]
    residual: list[float]
    initial_state: list[float] | None = None

    @field_validator("coefficients")
    @classmethod
    def _coefficients_valid(cls, v: list[float]) -> list[float]:
        if len(v) < 2:
            raise ValueError("coefficients must contain at least [1.0, a1]")
        if not all(math.isfinite(c) for c in v):
            raise ValueError("coefficients must be finite numbers")
        if abs(v[0] - 1.0) > 1e-6:
            raise ValueError("leading coefficient must be 1.0")
        return v

    @field_validator("residual")
    @classmethod
    def _residual_valid(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("residual must not be empty")
        if not all(math.isfinite(s) for s in v):
            raise ValueError("residual must be finite numbers")
        return v

    @model_validator(mode="after")
    def _state_length_matches(self) -> "SynthesizeRequest":
        if self.initial_state is not None:
            expected = len(self.coefficients) - 1
            if len(self.initial_state) != expected:
                raise ValueError(
                    f"initial_state must have {expected} samples "
                    f"(len(coefficients) - 1), got {len(self.initial_state)}"
                )
            if not all(math.isfinite(s) for s in self.initial_state):
                raise ValueError("initial_state must be finite numbers")
        return self


class StreamCreateRequest(BaseModel):
    order: int | None = None
    window: WindowName | None = None

    @field_validator("order")
    @classmethod
    def _order_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("order must be >= 1")
        return v


class StreamFrameRequest(BaseModel):
    samples: list[float]
    mode: Literal["analyze", "roundtrip"] = "roundtrip"

    @field_validator("samples")
    @classmethod
    def _samples_finite(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("samples must not be empty")
        if not all(math.isfinite(s) for s in v):
            raise ValueError("samples must be finite numbers (no NaN/inf)")
        return v


# ---------------------------------------------------------------- responses


class DiagnosticModel(BaseModel):
    code: str
    severity: str
    stage: str
    message: str


class MetaModel(BaseModel):
    request_id: str
    version: str
    pipeline_version: str
    stages: list[str]


class ToeplitzCrosscheckModel(BaseModel):
    max_abs_deviation: float | None
    agrees: bool
    note: str


class AnalysisPayload(BaseModel):
    order: int
    window: str
    frame_length: int
    frame_energy: float
    coefficients: list[float]
    reflection_coefficients: list[float]
    prediction_error_energy: float
    gain: float
    residual: list[float]
    residual_energy: float
    analysis_final_state: list[float]
    max_pole_magnitude: float | None
    stable: bool
    completed_order: int
    diagnostics: list[DiagnosticModel]
    errors: list[str]
    warnings: list[str]


class AnalyzeResponse(BaseModel):
    meta: MetaModel
    analysis: AnalysisPayload
    toeplitz_crosscheck: ToeplitzCrosscheckModel | None


class SynthesizeResponse(BaseModel):
    meta: MetaModel
    samples: list[float]
    final_state: list[float]


class ReconstructionMetricsModel(BaseModel):
    max_abs_error: float
    rms_error: float
    relative_error: float
    residual_energy_ratio: float | None
    verified: bool
    tolerance: float
    note: str


class RoundtripResponse(BaseModel):
    meta: MetaModel
    analysis: AnalysisPayload
    toeplitz_crosscheck: ToeplitzCrosscheckModel | None
    reconstructed: list[float]
    metrics: ReconstructionMetricsModel


class StreamCreateResponse(BaseModel):
    meta: MetaModel
    stream_id: str
    order: int
    window: str


class StreamStateResponse(BaseModel):
    meta: MetaModel
    stream_id: str
    order: int
    window: str
    frames_processed: int
    analysis_state: list[float]
    synthesis_state: list[float]


class StreamFrameResponse(BaseModel):
    meta: MetaModel
    stream_id: str
    frame_index: int
    mode: str
    analysis: AnalysisPayload
    reconstructed: list[float] | None
    metrics: ReconstructionMetricsModel | None
    frames_processed: int


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    request_id: str
    version: str
    error: ErrorBody


def validate_against_settings(payload: FrameParams, settings: Settings) -> None:
    """Bounds that depend on deployment settings (raised as domain errors)."""
    if len(payload.samples) > settings.max_frame_size:
        raise ValueError(
            f"frame of {len(payload.samples)} samples exceeds the maximum "
            f"of {settings.max_frame_size}"
        )
    order = payload.order if payload.order is not None else settings.default_order
    if order > settings.max_order:
        raise ValueError(f"order {order} exceeds the maximum of {settings.max_order}")
    if order >= len(payload.samples):
        raise ValueError(
            f"order {order} must be smaller than the frame length "
            f"{len(payload.samples)}"
        )
