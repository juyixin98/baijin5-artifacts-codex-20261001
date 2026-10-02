"""API request/response contracts (pydantic)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class ImagePayload(BaseModel):
    """One image: either a base64-encoded grayscale PNG or a fixture reference."""
    png_base64: Optional[str] = None
    fixture_id: Optional[str] = None
    role: Optional[str] = Field(
        default=None,
        description="required with fixture_id: 'reference' or 'moving'")

    @model_validator(mode="after")
    def exactly_one_source(self):
        if self.png_base64 and self.fixture_id:
            raise ValueError("provide either png_base64 or fixture_id, not both")
        if not self.png_base64 and not self.fixture_id:
            raise ValueError("provide png_base64 or fixture_id")
        if self.fixture_id and self.role not in ("reference", "moving"):
            raise ValueError("role must be 'reference' or 'moving' when fixture_id is used")
        return self


class ConfigOverrides(BaseModel):
    apply_window: Optional[bool] = None
    pad_factor: Optional[int] = Field(default=None, ge=1, le=4)
    min_overlap: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ncc_threshold: Optional[float] = Field(default=None, ge=-1.0, le=1.0)
    ambiguity_ratio: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class EstimateRequest(BaseModel):
    reference: ImagePayload
    moving: ImagePayload
    request_id: Optional[str] = None
    config: Optional[ConfigOverrides] = None


class ValidateRequest(BaseModel):
    fixture_id: Optional[str] = None   # omit to validate all fixtures
    tolerance_px: float = Field(default=0.5, gt=0)


class TiledEstimateRequest(BaseModel):
    reference: ImagePayload
    moving: ImagePayload
    request_id: Optional[str] = None
    tile_size: int = Field(default=64, ge=16)
    stride: int = Field(default=32, ge=8)
