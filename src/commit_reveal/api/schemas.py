"""Request/response schemas for the HTTP API.

Values and salts travel as 64-char lowercase hex (32 bytes). Deadlines are
ISO-8601 timestamps with an explicit timezone.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateRoundRequest(BaseModel):
    round_id: str | None = Field(default=None, min_length=1, max_length=64)
    participants: list[str] = Field(min_length=2, max_length=64)
    commit_deadline: str
    reveal_deadline: str
    min_reveals: int | None = Field(default=None, ge=1)


class CommitRequest(BaseModel):
    participant_id: str = Field(min_length=1, max_length=128)
    commitment: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class RevealRequest(BaseModel):
    participant_id: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    salt: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ErrorBody(BaseModel):
    category: str
    message: str
    request_id: str
