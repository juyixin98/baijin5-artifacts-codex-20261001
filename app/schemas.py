"""API sample contracts (request/response schemas).

These models are the data contract between the HTTP layer and the signal
layer: every field crossing a module boundary is typed and validated here,
and every failure crossing back is an :class:`app.errors.AppError` with a
machine-readable code.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class EstimateRequest(BaseModel):
    """One-shot FIR estimation request."""

    excitation: list[float] = Field(..., min_length=2, description="known input signal x[n]")
    response: list[float] = Field(..., min_length=2, description="measured output signal y[n]")
    model_order: int = Field(..., ge=1, description="FIR order L (number of taps)")
    regularization: float | None = Field(
        default=None, ge=0.0, description="Tikhonov lambda; null uses the server default"
    )
    delay: int = Field(default=0, ge=0, description="explicit response lag in samples")
    estimate_delay: bool = Field(
        default=False, description="estimate the lag via cross-correlation instead of using `delay`"
    )
    boundary: Literal["valid", "zero_pad"] = Field(
        default="valid", description="boundary mode of the convolution matrix"
    )
    holdout_fraction: float = Field(
        default=0.25, ge=0.0, lt=1.0, description="fraction of rows held out for prediction error"
    )
    require_identifiable: bool = Field(
        default=False,
        description="fail with IDENTIFIABILITY_FAILURE when the excitation is rank-deficient",
    )


class IdentifiabilityInfo(BaseModel):
    n_rows: int
    n_columns: int
    singular_values: list[float]
    rank: int
    condition_number: float
    tolerance: float
    identifiable: bool


class Metrics(BaseModel):
    n_samples: int
    mse: float
    rmse: float


class EstimateResponse(BaseModel):
    run_id: str
    coefficients: list[float]
    order: int
    boundary: str
    delay: int
    delay_source: str
    regularization: float
    identifiable: bool
    identifiability: IdentifiabilityInfo
    train_metrics: Metrics
    holdout_metrics: Metrics | None
    warnings: list[str]


class ErrorBody(BaseModel):
    code: str
    reason: str
    message: str
    detail: dict[str, Any] = {}
    run_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class SessionCreateResponse(BaseModel):
    session_id: str
    state: str


class SessionStateResponse(BaseModel):
    session_id: str
    state: str
    created_utc: str
    n_samples: int
    n_chunks: int


class ChunkRequest(BaseModel):
    excitation: list[float] = Field(..., min_length=1)
    response: list[float] = Field(..., min_length=1)


class FinalizeRequest(BaseModel):
    model_order: int = Field(..., ge=1)
    regularization: float | None = Field(default=None, ge=0.0)
    delay: int = Field(default=0, ge=0)
    estimate_delay: bool = False
    boundary: Literal["valid", "zero_pad"] = "valid"
    holdout_fraction: float = Field(default=0.25, ge=0.0, lt=1.0)
    require_identifiable: bool = False
