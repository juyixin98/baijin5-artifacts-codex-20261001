"""Pydantic API contract."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..core.contracts import (
    LeakagePolicy,
    MissingPolicy,
    SEType,
    ThetaSource,
    ZeroVariancePolicy,
)


class AnalysisRequest(BaseModel):
    name: str = Field(default="unnamed_experiment", min_length=1, max_length=200)
    outcome_column: str = Field(min_length=1)
    treatment_column: str = Field(min_length=1)
    covariates: list[str] = Field(default_factory=list)
    # Explicit provenance: map covariate name -> True if measured BEFORE
    # treatment. Post-treatment fields are flagged or rejected; absence means
    # pre-treatment (recorded in the run).
    pre_treatment_covariates: dict[str, bool] = Field(default_factory=dict)
    data: dict[str, list[Any]] = Field(min_length=1)

    theta_source: ThetaSource = ThetaSource.CONTROL_PRE
    # Required iff theta_source='given': externally supplied (e.g. pre-registered
    # or prior-experiment) coefficient, one entry per covariate, in covariate
    # list order. Fitted sources ignore this.
    given_theta: list[float] | None = Field(default=None)
    # SE family for the grouping-design estimators (unadjusted, CUPED).
    # The Lin/ANCOVA result always reports HC1; regression SEs are not a valid
    # value here and are rejected at the schema boundary.
    se_type: SEType = SEType.WELCH
    regression_interactions: bool = True
    missing_policy: MissingPolicy = MissingPolicy.FAIL
    zero_variance_policy: ZeroVariancePolicy = ZeroVariancePolicy.DROP
    leakage_policy: LeakagePolicy = LeakagePolicy.FLAG
    smd_threshold: float = Field(default=0.20, gt=0.0)
    alpha: float = Field(default=0.01, gt=0.0, lt=0.5)
    ci_level: float = Field(default=0.95, gt=0.0, lt=1.0)

    @field_validator("se_type")
    @classmethod
    def _se_type_must_match_design(cls, v: SEType) -> SEType:
        if v not in (SEType.WELCH, SEType.POOLED):
            raise ValueError(
                "se_type must be 'welch' or 'pooled' for the unadjusted/CUPED "
                "estimators; the Lin result always reports HC1 robust SE"
            )
        return v

    @field_validator("data")
    @classmethod
    def _columns_non_empty(cls, v: dict[str, list]) -> dict[str, list]:
        empty = [name for name, col in v.items() if len(col) == 0]
        if empty:
            raise ValueError(f"columns must be non-empty: {empty}")
        return v


class ErrorResponse(BaseModel):
    error: dict[str, Any]


class RunSummary(BaseModel):
    run_id: str
    data_fingerprint: str
    created_at: str
    experiment_name: str
    n_rows: int
    unadjusted_estimate: float | None
    unadjusted_se: float | None
    cuped_estimate: float | None
    cuped_se: float | None
    lin_estimate: float | None
    lin_se: float | None
