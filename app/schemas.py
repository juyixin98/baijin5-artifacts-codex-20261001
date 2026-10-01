"""Pydantic request/response contracts for the HTTP layer.

These models are the external data boundary; they validate SHAPE, while the
statistical kernel validates MEANING (e.g. p-value range). Invalid input never
reaches the kernel with an unchecked value.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .statistics import (
    CONTRACT_VERSION,
    DEFAULT_ALPHA,
    DEFAULT_W0_FRACTION,
    SCHEDULE_C,
    SCHEDULE_HORIZON,
)

_HYP_ID_RE = re.compile(r"^[A-Za-z0-9_.\-:/]{1,128}$")


def _validate_hypothesis_id(v: str) -> str:
    if not isinstance(v, str) or not _HYP_ID_RE.fullmatch(v):
        raise ValueError(
            "hypothesis_id must be 1-128 chars from [A-Za-z0-9_.-:/]"
        )
    return v


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alpha: float | None = Field(default=None, description="target online FDR level")
    w0: float | None = Field(default=None, description="initial wealth, 0 < w0 <= alpha")
    horizon: int | None = Field(default=None, description="frozen max number of hypotheses")
    note: str = Field(default="", max_length=500)
    run_id: str | None = Field(default=None, max_length=64)


class ReserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str

    @field_validator("hypothesis_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_hypothesis_id(v)


class DecideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    p_value: float

    @field_validator("hypothesis_id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_hypothesis_id(v)


class RunOut(BaseModel):
    run_id: str
    contract_version: str
    alpha: float
    w0: float
    payoff: float
    horizon: int
    status: str
    note: str
    head_hash: str


class StepOut(BaseModel):
    run_id: str
    idx: int
    hypothesis_id: str
    threshold: float
    wealth_before: float
    gamma_t: float
    status: Literal["pending", "decided"]
    p_value: float | None
    rejected: bool | None
    wealth_after: float | None
    reserved_at: float
    decided_at: float | None
    prev_hash: str | None
    row_hash: str | None


class ReplayStep(BaseModel):
    idx: int
    hypothesis_id: str
    threshold: float
    p_value: float
    rejected: bool
    wealth_before: float


class ReplayOut(BaseModel):
    run_id: str
    contract_version: str
    decisions_checked: int
    rejections: int
    head_hash: str
    steps: list[ReplayStep]


class EventOut(BaseModel):
    seq: int
    kind: str
    code: str | None
    message: str
    payload: dict[str, Any]
    created_at: float


class ContractOut(BaseModel):
    contract_version: str
    rule: str
    alpha_default: float
    w0_default_fraction: float
    w0_default: float
    payoff_default: float
    horizon_default: int
    schedule_formula: str
    schedule_constant_c: float
    decision_rule: str
    assumptions: list[str]
    not_guaranteed: list[str]


CONTRACT_ASSUMPTIONS = [
    "Hypotheses arrive in an a-priori-ordered, fixed sequence; the decision on "
    "hypothesis t is made before p_{t+1} is observed.",
    "Each p-value is valid (super-uniform under its null): a true-null p-value "
    "is stochastically >= Uniform(0,1).",
    "p-values are either mutually independent, or satisfy standard local "
    "dependence (PRDS-style) conditions under which LORD++ FDR control holds.",
    "The sequence length does not exceed the declared, frozen horizon H; "
    "gamma is normalized on 1..H.",
    "Parameters alpha, w0 and the reward schedule are frozen at run creation; "
    "0 < w0 <= alpha, payoff b = alpha - w0.",
]

CONTRACT_NOT_GUARANTEED = [
    "Any single run need not attain FDR <= alpha; FDR is an expectation over "
    "repeated experiments, demonstrated here by Monte Carlo aggregation only.",
    "Offline BH / fixed-sequence batch procedures are NOT equivalent and must "
    "not be substituted: thresholds here are causal functions of past results.",
    "No control is claimed for arbitrary dependence, adaptive ordering chosen "
    "after seeing p-values, post-hoc edited p-values, or p-values exceeding "
    "the frozen horizon.",
]


def contract_payload() -> ContractOut:
    return ContractOut(
        contract_version=CONTRACT_VERSION,
        rule="LORD++ (Ramdas, Zrnic & Wainwright 2018), online FDR",
        alpha_default=DEFAULT_ALPHA,
        w0_default_fraction=DEFAULT_W0_FRACTION,
        w0_default=DEFAULT_ALPHA * DEFAULT_W0_FRACTION,
        payoff_default=DEFAULT_ALPHA * (1 - DEFAULT_W0_FRACTION),
        horizon_default=SCHEDULE_HORIZON,
        schedule_formula=(
            "gamma(j) = [C*log(max(j,2))/max(j,2)] / S_H, "
            "S_H = sum_{i=1..H} C*log(max(i,2))/max(i,2)"
        ),
        schedule_constant_c=SCHEDULE_C,
        decision_rule=(
            "W_t = w0 + sum_{tau_k<t} gamma(t-tau_k)*(b-alpha_{tau_k}); "
            "alpha_t = gamma(t)*W_t; reject iff p_t <= alpha_t "
            "(alpha_{tau_k} is the threshold spent on past rejection k, "
            "frozen before its p-value was observed)"
        ),
        assumptions=CONTRACT_ASSUMPTIONS,
        not_guaranteed=CONTRACT_NOT_GUARANTEED,
    )
