"""Pydantic request/response models for the HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TransactionIn(BaseModel):
    tid: str = Field(..., min_length=1, description="Unique transaction identity within the batch")
    items: list[str] = Field(default_factory=list, description="Items; repeats within one tid count once")


class DatasetIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    transactions: list[TransactionIn] = Field(..., min_length=1)


class DatasetStats(BaseModel):
    transaction_count: int
    distinct_item_count: int
    empty_transaction_count: int
    duplicate_transaction_count: int


class DatasetOut(BaseModel):
    dataset_id: str
    name: str | None
    content_hash: str
    engine_version: str
    stats: DatasetStats


class JobCreate(BaseModel):
    dataset_id: str
    min_support: int = Field(..., ge=1, description="Integer support threshold (number of transactions)")
    budget: int | None = Field(default=None, ge=0, description="Candidate evaluations for the first chunk (0 evaluates only the root)")


class ResumeIn(BaseModel):
    budget: int | None = Field(default=None, ge=0)


class ItemsetResult(BaseModel):
    itemset: tuple[str, ...]
    support: int


class JobOut(BaseModel):
    job_id: str
    request_id: str
    dataset_id: str
    dataset_hash: str
    engine_version: str
    min_support: int
    status: Literal["running", "complete"]
    complete: bool
    evaluations_used: int
    closed_itemsets: list[ItemsetResult]
    maximal_itemsets: list[ItemsetResult]
    maximal_results_certain: bool
    notes: list[str]


class ChunkOut(BaseModel):
    job: JobOut
    chunk: dict


class ErrorOut(BaseModel):
    error_code: str
    message: str
    request_id: str
    engine_version: str
    details: dict | None = None
