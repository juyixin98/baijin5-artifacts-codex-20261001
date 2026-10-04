"""Image data contracts (request/response models).

The contract is deliberately explicit about failure categories so a client
can distinguish bad input from empty results from numerically uncertain
results.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    INVALID_SHAPE = "INVALID_SHAPE"
    INVALID_IMAGE = "INVALID_IMAGE"
    INVALID_SPACING = "INVALID_SPACING"
    TOO_LARGE = "TOO_LARGE"
    EMPTY_RASTER = "EMPTY_RASTER"
    UNSUPPORTED_ENCODING = "UNSUPPORTED_ENCODING"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FailureItem(BaseModel):
    code: ErrorCode
    message: str
    # Machine-oriented location, e.g. "body.spacing_y" or "image.decode".
    location: str = ""


class WarningItem(BaseModel):
    code: str
    message: str


class EDTRequest(BaseModel):
    # Base64-encoded PNG (mode "1", "L", or "P"); nonzero/non-black = source.
    image_base64: str = Field(..., description="Base64 PNG; source = nonzero pixels")
    spacing_y: float = Field(1.0, gt=0.0)
    spacing_x: float = Field(1.0, gt=0.0)
    source_value: Literal["nonzero", "white", "black"] = "nonzero"
    # Force the tiled execution path even below the size threshold.
    force_tiled: bool = False
    request_id: str | None = Field(
        None, description="Optional caller correlation id; echoed in response/logs"
    )


class StatsModel(BaseModel):
    rows: int
    columns: int
    source_count: int
    min_distance: float | None
    max_distance: float | None
    mean_distance: float | None
    has_sources: bool
    all_sources: bool


class EDTResponse(BaseModel):
    request_id: str
    kernel_version: str
    spacing_y: float
    spacing_x: float
    stats: StatsModel
    # Base64 PNGs: float32 distance map and int32 nearest-source coordinates.
    distance_png_base64: str
    nearest_y_png_base64: str
    nearest_x_png_base64: str
    # JSON-encoded arrays for small rasters (handy for verification clients).
    # null entries mark +inf cells (raster with no sources).
    distance_grid: list[list[float | None]] | None = None
    nearest_y_grid: list[list[int]] | None = None
    nearest_x_grid: list[list[int]] | None = None
    execution: dict
    failures: list[FailureItem] = Field(default_factory=list)
    warnings: list[WarningItem] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    kernel_version: str
