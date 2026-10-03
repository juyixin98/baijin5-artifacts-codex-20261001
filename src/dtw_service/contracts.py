"""Sample contracts: request/response schemas and the status vocabulary.

These models are the public boundary of the service. Validation failures here
are the "rejected" failure category; an unreachable endpoint under the fixed
constraints is the "unreachable" category and is reported as a structured
response, not an exception leak.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Fixed by the behavior contract; exposed as Literals so requests cannot
# silently renegotiate them.
MetricName = Literal["euclidean"]
StepPatternName = Literal["symmetric"]
NormalizationName = Literal["path_length"]

STATUS_OK = "ok"
STATUS_UNREACHABLE = "unreachable"


class AlignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: list[list[float]] = Field(min_length=1)
    reference: list[list[float]] = Field(min_length=1)
    window: int | None = Field(default=None, ge=0)
    max_run: int | None = Field(default=None, ge=1)
    request_id: str | None = Field(default=None, max_length=64)

    @field_validator("query", "reference")
    @classmethod
    def frames_must_be_finite_vectors(cls, frames: list[list[float]]) -> list[list[float]]:
        for frame in frames:
            if not frame:
                raise ValueError("frames must be non-empty vectors")
            if not all(np.isfinite(v) for v in frame):
                raise ValueError("frames must contain only finite values")
        return frames

    @model_validator(mode="after")
    def dimensions_must_match(self) -> "AlignRequest":
        q_dim = len(self.query[0])
        r_dim = len(self.reference[0])
        if any(len(f) != q_dim for f in self.query) or any(
            len(f) != r_dim for f in self.reference
        ):
            raise ValueError("all frames within a sequence must share one dimension")
        if q_dim != r_dim:
            raise ValueError(
                f"feature dimensions differ: query has {q_dim}, reference has {r_dim}"
            )
        return self


class StretchPoint(BaseModel):
    path_index: int
    query_index: int
    reference_index: int
    rate: float | None  # null where the local rate is unbounded (vertical segment)


class AlignResponse(BaseModel):
    request_id: str
    status: Literal["ok", "unreachable"]
    reason: str
    metric: MetricName = "euclidean"
    step_pattern: StepPatternName = "symmetric"
    normalization: NormalizationName = "path_length"
    window: int
    max_run: int
    path: list[tuple[int, int]] | None = None
    total_cost: float | None = None
    normalized_cost: float | None = None
    path_length: int | None = None
    mean_stretch_rate: float | None = None
    stretch: list[StretchPoint] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    normalization: NormalizationName = "path_length"
