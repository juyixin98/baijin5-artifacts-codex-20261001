"""Pydantic request/response schemas for the verification HTTP API."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class GradCheckRequest(BaseModel):
    scenario: str = Field(
        ..., description="Registered synthetic fixture scenario id."
    )
    eps: Optional[float] = Field(None, gt=0, description="FD perturbation step")
    atol: Optional[float] = Field(None, ge=0)
    rtol: Optional[float] = Field(None, ge=0)
    request_id: Optional[str] = Field(
        None, max_length=64,
        description="Caller correlation id; generated when omitted.",
    )


class LeafReport(BaseModel):
    name: str
    status: str
    category: Optional[str] = None
    max_abs_err: float
    max_rel_err: float
    shape: list[int]
    worst_index: Optional[list[int]] = None
    analytic_preview: list[float] = Field(default_factory=list)
    numeric_preview: list[float] = Field(default_factory=list)


class DiagnosticEntry(BaseModel):
    record_id: str
    request_id: str
    event: str
    status: str
    reason: str
    state: dict[str, Any] = Field(default_factory=dict)


class GradCheckResponse(BaseModel):
    request_id: str
    scenario: str
    status: str
    eps: float
    atol: float
    rtol: float
    leaves: list[LeafReport]
    diagnostics: list[DiagnosticEntry]


class InplaceCheckRequest(BaseModel):
    request_id: Optional[str] = Field(None, max_length=64)
    mutate: bool = Field(
        True, description="If true, mutate a captured input before backward."
    )


class InplaceCheckResponse(BaseModel):
    request_id: str
    accepted: bool
    failure_category: Optional[str]
    message: str
    diagnostics: list[DiagnosticEntry]


class HealthResponse(BaseModel):
    status: str
    service: str
    scenarios: list[str]
