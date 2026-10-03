"""Pydantic schemas for the HTTP boundary (request/response contracts)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "uncertain", "failed"]


class EstimateRequest(BaseModel):
    image_a: str = Field(description="base64-encoded 8-bit grayscale PNG")
    image_b: str = Field(description="base64-encoded 8-bit grayscale PNG")
    window: Literal["hann", "none"] | None = Field(
        default=None, description="override the configured apodization window"
    )


class ShiftModel(BaseModel):
    dy: float
    dx: float


class PeakModel(BaseModel):
    dy: int
    dx: int
    value: float


class EstimateResponse(BaseModel):
    request_id: str
    status: Status
    shift: ShiftModel | None
    integer_shift: ShiftModel | None
    subpixel_offset: ShiftModel | None
    confidence: float
    psr: float | None
    second_peak_ratio: float | None
    overlap_fraction: float | None
    peaks: list[PeakModel]
    ambiguity_peaks: list[PeakModel]
    failures: list[str]
    uncertainties: list[str]
    diagnostics: dict[str, Any]


class TileResultModel(BaseModel):
    origin: tuple[int, int]
    status: Status
    shift: ShiftModel | None
    confidence: float
    failures: list[str]
    uncertainties: list[str]


class TiledJobResponse(BaseModel):
    request_id: str
    status: Status
    global_shift: ShiftModel | None
    spread_px: float | None
    tiles_total: int
    tiles_used: int
    tiles: list[TileResultModel]
    failures: list[str]
    uncertainties: list[str]


class FixtureInfo(BaseModel):
    name: str
    category: str
    ground_truth_shift: ShiftModel | None
    expected_status: str
    note: str


class FixtureDetail(FixtureInfo):
    image_a: str
    image_b: str


class HealthResponse(BaseModel):
    status: str
    versions: dict[str, str]


class ValidationResponse(BaseModel):
    request_id: str
    report: dict[str, Any]


class ErrorResponse(BaseModel):
    detail: str
    request_id: str
