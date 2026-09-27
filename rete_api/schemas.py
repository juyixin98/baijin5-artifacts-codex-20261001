"""Pydantic request models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

_ALLOWED = (str, int, float, bool, type(None))


class ConditionIn(BaseModel):
    kind: str = Field(min_length=1)
    fields: list[Any] = Field(default_factory=list)


class ActionIn(BaseModel):
    op: str
    kind: str | None = None
    fields: list[Any] = Field(default_factory=list)
    tag: str | None = None


class RuleIn(BaseModel):
    name: str = Field(min_length=1)
    conditions: list[ConditionIn]
    actions: list[ActionIn] = Field(default_factory=list)
    tests: list[list[Any]] = Field(default_factory=list)
    salience: int = 0


class FactIn(BaseModel):
    kind: str = Field(min_length=1)
    fields: list[Any] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def _scalar_fields(cls, values: list[Any]) -> list[Any]:
        for value in values:
            if not isinstance(value, _ALLOWED):
                raise ValueError(
                    "fact fields must be JSON scalars, got "
                    f"{type(value).__name__}")
        return values


class RunIn(BaseModel):
    max_cycles: int | None = Field(default=None, ge=1)
