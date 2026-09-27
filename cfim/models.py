"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CorpusCreateRequest(BaseModel):
    name: str = Field(..., description="Unique, non-empty corpus identifier.")
    transactions: list[list[str]] = Field(
        ..., description="One transaction per element; each is a list of item strings."
    )


class CorpusResponse(BaseModel):
    corpus_id: str
    name: str
    transaction_count: int
    item_count: int
    empty_transaction_count: int
    duplicate_item_occurrences: int
    kernel_version: str
    created_at: str


class JobCreateRequest(BaseModel):
    min_support: int = Field(
        ..., ge=1, description="Absolute integer threshold on containing transactions."
    )
    budget: int | None = Field(
        default=None, ge=1, description="DFS node-visit budget for this first run slice."
    )


class JobAdvanceRequest(BaseModel):
    budget: int | None = Field(
        default=None, ge=1, description="Additional DFS node-visit budget."
    )


class ItemsetResponse(BaseModel):
    itemset: list[str]
    support: int
    maximal: bool


class JobResponse(BaseModel):
    job_id: str
    corpus_id: str
    min_support: int
    status: str = Field(description="RUNNING (partial results available) or COMPLETED.")
    partial: bool = Field(
        description="True iff the enumeration budget was exhausted before completion."
    )
    nodes_visited: int
    total_budget_used: int
    budget_unit: str
    closed_count: int
    results: list[ItemsetResponse]
    kernel_version: str
    created_at: str
    updated_at: str


class QueryRequest(BaseModel):
    items: list[str] = Field(
        ..., description="Itemset to verify independently against the stored corpus."
    )
    min_support: int | None = Field(
        default=None,
        ge=1,
        description="Optional threshold used only to label frequent/closed status.",
    )


class QueryResponse(BaseModel):
    corpus_id: str
    itemset: list[str]
    support: int
    transaction_count: int
    is_frequent: bool | None = Field(
        description="Null when no min_support was supplied."
    )
    closure: list[str]
    closure_support: int
    is_closed: bool
    same_support_supersets: list[list[str]] = Field(
        description="Non-empty only for a non-closed itemset: the containing "
        "same-support closure shows exactly why it fails the closed test."
    )
    computed_by: str
    kernel_version: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorBody
    request_id: str | None = None
