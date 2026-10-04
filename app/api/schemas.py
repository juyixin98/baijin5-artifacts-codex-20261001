"""Pydantic request/response schemas for the HTTP transport."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

MESSAGE_ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"


class BeginStreamRequest(BaseModel):
    message_id: Annotated[str, Field(pattern=MESSAGE_ID_PATTERN)]
    total_segments: Annotated[int, Field(ge=1, le=2**63 - 1)]
    total_len: Annotated[int, Field(ge=0, le=2**63 - 1)]


class SegmentRequest(BaseModel):
    # One canonical v1 frame, base64-encoded (standard, padded).
    frame_b64: Annotated[str, Field(min_length=1)]

    @field_validator("frame_b64")
    @classmethod
    def _not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("frame_b64 is empty")
        return v


class AcceptanceResponse(BaseModel):
    accepted: bool
    complete: bool
    message_id: str
    seqno: int
    received: int
    total_segments: int
    replay: bool = False


class StatusResponse(BaseModel):
    message_id: str
    status: str
    total_segments: int
    total_len: int
    received_count: int
    received_seqnos: list[int]
    have_terminator: bool
    released: bool


class ErrorResponse(BaseModel):
    category: str
    error: str
    request_id: str | None = None
    state: dict[str, object] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    id: int
    ts: float
    message_id: str | None
    kind: str
    category: str
    request_id: str | None
    message: str
    state: dict[str, object]
