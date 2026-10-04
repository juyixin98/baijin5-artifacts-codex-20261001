from __future__ import annotations

from pydantic import BaseModel, Field


class PutRecordRequest(BaseModel):
    field: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    value: str | None  # required but nullable: null means "store NULL"


class QueryRequest(BaseModel):
    field: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    value: str | None


class RotateRequest(BaseModel):
    # Test/dev hook: stop the reindex loop after N records to simulate an
    # interrupted rotation. None (default) runs to completion.
    crash_after: int | None = Field(default=None, ge=0)
