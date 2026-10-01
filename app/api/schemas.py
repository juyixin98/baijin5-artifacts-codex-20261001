"""Pydantic request/response schemas (the HTTP statistical contract)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CovariateDeclarationIn(BaseModel):
    name: str = Field(..., min_length=1, examples=["x_pre"])
    pre_treatment: bool = Field(
        ..., description="False marks a post-treatment/leakage field; such fields are rejected"
    )


class RegisterExperimentIn(BaseModel):
    experiment_id: str = Field(..., min_length=1)
    description: str = ""
    covariates: list[CovariateDeclarationIn]


class ObservationIn(BaseModel):
    unit_id: str = Field(..., min_length=1)
    treatment: Literal[0, 1]
    outcome: float
    covariates: dict[str, float | None] = Field(default_factory=dict)


class UploadIn(BaseModel):
    observations: list[ObservationIn]


class RunEstimationIn(BaseModel):
    covariate_names: list[str] | None = None
    missing_strategy: Literal["error", "mean_impute"] = "mean_impute"
    zero_variance_strategy: Literal["drop", "error"] = "drop"
    theta_source: Literal["control", "pooled_fwl"] = "control"


class ErrorBody(BaseModel):
    error_code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
