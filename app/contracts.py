"""Sample contracts: request/response schemas for the HTTP boundary.

Transport is fixture-grade JSON float arrays (float64 round-trip).  This is
deliberate: the service targets local synthetic workloads and test
reproducibility, not production audio throughput.  Binary streaming would be
the production shape; the boundary semantics documented in the README are
identical either way.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    sample_rate: int = Field(gt=0, le=1_000_000)
    block_size: int = Field(ge=8, le=65536, description="power of two")
    ir: list[float] = Field(min_length=1, max_length=2_000_000)
    swap_strategy: Literal["restart", "crossfade"] = "crossfade"
    crossfade_blocks: int = Field(default=4, ge=1, le=1024)
    max_state_bytes: int | None = Field(default=None, gt=0)


class StateBytesReport(BaseModel):
    ir_spectra: int
    input_fdl: int
    overlap_tail: int
    crossfade_old: int = 0
    total: int


class CreateSessionResponse(BaseModel):
    session_id: str
    request_id: str
    block_size: int
    ir_length: int
    num_partitions: int
    state_bytes: StateBytesReport
    budget_bytes: int | None


class BlockRequest(BaseModel):
    samples: list[float] = Field(min_length=1, max_length=65536)
    final: bool = False


class BlockResponse(BaseModel):
    session_id: str
    request_id: str
    output: list[float]
    output_length: int
    blocks_processed: int


class FlushResponse(BaseModel):
    session_id: str
    request_id: str
    tail: list[float]
    tail_length: int
    total_input_samples: int
    total_output_samples: int


class SwapIRRequest(BaseModel):
    ir: list[float] = Field(min_length=1, max_length=2_000_000)
    strategy: Literal["restart", "crossfade"] | None = None


class SwapIRResponse(BaseModel):
    session_id: str
    request_id: str
    strategy: str
    ir_length: int
    num_partitions: int
    state_bytes: StateBytesReport


class SessionStateResponse(BaseModel):
    session_id: str
    request_id: str
    sample_rate: int
    block_size: int
    ir_length: int
    num_partitions: int
    blocks_processed: int
    total_input_samples: int
    total_output_samples: int
    final_received: bool
    flushed: bool
    fade_remaining_blocks: int
    state_bytes: StateBytesReport
    budget_bytes: int | None


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody
