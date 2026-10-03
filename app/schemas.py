"""Request/response contracts for the HTTP API.

Pydantic models enforce the sample contract at the boundary: finite
floats only, bounded block sizes, half-open freeze intervals. Anything
rejected here maps to the ``input_error`` category.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import DEFAULT_SETTINGS
from app.dsp.lms import ALGORITHMS

_S = DEFAULT_SETTINGS


def _all_finite(values: list[float]) -> list[float]:
    for v in values:
        if not math.isfinite(v):
            raise ValueError("samples must be finite numbers (no NaN/inf)")
    return values


class CreateChannelRequest(BaseModel):
    algorithm: Literal["lms", "nlms"]
    filter_len: int = Field(ge=_S.filter_len_min, le=_S.filter_len_max)
    mu: float = Field(gt=0.0, lt=2.0)
    eps: float = Field(default=_S.eps_default, ge=_S.eps_min)
    channel_id: str | None = Field(default=None, min_length=1, max_length=64)


class ChannelResponse(BaseModel):
    channel_id: str
    algorithm: str
    filter_len: int
    mu: float
    eps: float
    samples_processed: int


class FreezeInterval(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class ProcessRequest(BaseModel):
    reference: list[float] = Field(min_length=1, max_length=_S.max_block_samples)
    primary: list[float] = Field(min_length=1, max_length=_S.max_block_samples)
    freeze_intervals: list[FreezeInterval] = Field(default_factory=list)

    _finite_ref = field_validator("reference")(_all_finite)
    _finite_pri = field_validator("primary")(_all_finite)


class ProcessResponse(BaseModel):
    run_id: str
    channel_id: str
    samples: int
    adapted_samples: int
    frozen_samples: int
    output: list[float]
    error: list[float]
    min_denominator: float
    mean_reference_energy: float
    weight_norm_before: float
    weight_norm_after: float


class ChannelStateResponse(BaseModel):
    channel_id: str
    weights: list[float]
    buffer: list[float]
    samples_processed: int


class EvaluateRequest(BaseModel):
    scenario: str
    algorithm: Literal["lms", "nlms"] = "nlms"
    n_samples: int = Field(default=4000, ge=64, le=_S.max_eval_samples)
    filter_len: int = Field(default=4, ge=_S.filter_len_min, le=_S.filter_len_max)
    mu: float = Field(default=0.05, gt=0.0, lt=2.0)
    eps: float = Field(default=_S.eps_default, ge=_S.eps_min)
    seed: int = Field(default=42, ge=0)
    freeze_intervals: list[FreezeInterval] = Field(default_factory=list)


class EvaluateResponse(BaseModel):
    run_id: str
    scenario: str
    algorithm: str
    n_samples: int
    seed: int
    snr_improvement_db: float
    mse_residual_vs_clean: float
    coefficient_error_norm: float
    final_weights: list[float]
    true_coeffs: list[float]
    meta: dict


class ErrorEnvelope(BaseModel):
    error: dict


ALGORITHM_LIST = list(ALGORITHMS)
