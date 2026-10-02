"""Pydantic schemas for the validation interface."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SeedPointSchema(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    label: int = Field(ge=1)


class SegmentRequest(BaseModel):
    gradient: list[list[float]]
    seeds: list[SeedPointSchema] | None = None
    markers: list[list[int]] | None = None
    connectivity: Literal[4, 8] | None = None
    mask: list[list[bool]] | None = None

    @model_validator(mode="after")
    def exactly_one_marker_source(self) -> "SegmentRequest":
        if (self.seeds is None) == (self.markers is None):
            raise ValueError("provide exactly one of 'seeds' or 'markers'")
        return self


class ImageSegmentRequest(BaseModel):
    """Segmentation with the gradient supplied as a base64 16-bit PNG."""

    gradient_png_b64: str
    seeds: list[SeedPointSchema]
    connectivity: Literal[4, 8] | None = None
