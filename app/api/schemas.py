"""Pydantic request/response schemas for the RD API."""
from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator

KernelName = Literal["triangular", "epanechnikov", "uniform", "tricube"]
SEType = Literal["const", "hc1", "hc2"]


class RDEstimateRequest(BaseModel):
    x: List[float] = Field(..., description="running variable observations")
    y: List[float] = Field(..., description="outcome observations (paired with x)")
    cutoff: float = 0.0
    kernel: KernelName = "triangular"
    bandwidth: Union[Literal["rot", "ik"], float] = Field(
        "rot", description="'rot', 'ik', or a positive fixed float"
    )
    se_type: SEType = "hc2"
    run_id: Optional[
        Annotated[str, Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")]
    ] = None
    alpha: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.05

    @field_validator("x", "y")
    @classmethod
    def _finite_series(cls, v: List[float]) -> List[float]:
        if len(v) < 6:
            raise ValueError("need at least 6 observations (3 per side minimum)")
        if any(val != val or val in (float("inf"), float("-inf")) for val in v):
            raise ValueError("x/y must contain only finite values")
        return v

    @field_validator("bandwidth")
    @classmethod
    def _positive_bw(cls, v: Union[str, float]) -> Union[str, float]:
        if isinstance(v, float) and v <= 0:
            raise ValueError("fixed bandwidth must be positive")
        return v
