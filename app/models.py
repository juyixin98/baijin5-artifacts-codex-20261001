"""Pydantic schemas for the API boundary.

Schema-level validation (types, required fields) happens here; domain-level
validation (allele alphabet, referential integrity, quality bounds) happens
in app.parsing so that failures carry a FailureCategory.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SiteIn(BaseModel):
    id: str = Field(min_length=1)
    chrom: str = Field(min_length=1)
    position: int = Field(ge=0)
    ref: str = Field(min_length=1)
    alt: str = Field(min_length=1)


class ReadCallIn(BaseModel):
    site: str = Field(min_length=1)
    allele: Literal["ref", "alt", "unknown"]
    qual: int


class ReadIn(BaseModel):
    id: str = Field(min_length=1)
    calls: list[ReadCallIn] = Field(min_length=1)


class PhaseRequest(BaseModel):
    sample_id: str = Field(min_length=1)
    sites: list[SiteIn] = Field(min_length=1)
    reads: list[ReadIn] = Field(min_length=1)
