"""Pydantic request/response schemas and translation into core contracts."""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .config import Settings
from .contracts import PairwiseSpec, Seed, SegmentationSpec, build_spec, validate_pairwise
from .errors import InputValidationError
from .imaging import (
    IntensityQuadraticModel,
    decode_array_image,
    decode_png_base64,
)


class ImagePayload(BaseModel):
    format: Literal["array", "png_base64"]
    data: Any  # list[list[number]] for "array", base64 string for "png_base64"


class DataModelPayload(BaseModel):
    type: Literal["intensity_quadratic"] = "intensity_quadratic"
    fg_mean: float = 200.0
    bg_mean: float = 60.0
    sigma: float = 25.0


class UnariesPayload(BaseModel):
    unary0: list[list[float]] = Field(description="per-pixel cost of label 0")
    unary1: list[list[float]] = Field(description="per-pixel cost of label 1")


class PairwisePayload(BaseModel):
    type: Literal["potts", "table"] = "potts"
    weight: float | None = Field(default=1.0, description="Potts weight for type=potts")
    v00: float | None = None
    v01: float | None = None
    v10: float | None = None
    v11: float | None = None

    def to_spec(self) -> PairwiseSpec:
        if self.type == "potts":
            w = 1.0 if self.weight is None else self.weight
            return validate_pairwise(0.0, w, w, 0.0)
        missing = [n for n in ("v00", "v01", "v10", "v11")
                   if getattr(self, n) is None]
        if missing:
            raise InputValidationError(
                f"pairwise table requires {missing}",
                code="PAIRWISE_TABLE_INCOMPLETE",
                details={"missing": missing},
            )
        return validate_pairwise(self.v00, self.v01, self.v10, self.v11)  # type: ignore[arg-type]


class SeedPayload(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    label: Literal[0, 1]


class SegmentRequest(BaseModel):
    image: ImagePayload | None = None
    data_model: DataModelPayload | None = None
    unaries: UnariesPayload | None = None
    pairwise: PairwisePayload = Field(default_factory=PairwisePayload)
    seeds: list[SeedPayload] = Field(default_factory=list)


def _unaries_from_image(req: SegmentRequest) -> tuple[np.ndarray, np.ndarray]:
    assert req.image is not None
    model_payload = req.data_model or DataModelPayload()
    if req.image.format == "array":
        intensity = decode_array_image(req.image.data)
    else:
        if not isinstance(req.image.data, str):
            raise InputValidationError(
                "png_base64 image data must be a base64 string",
                code="IMAGE_FORMAT_INVALID",
            )
        intensity = decode_png_base64(req.image.data)
    model = IntensityQuadraticModel(
        fg_mean=model_payload.fg_mean,
        bg_mean=model_payload.bg_mean,
        sigma=model_payload.sigma,
    )
    return model.unaries(intensity)


def request_to_spec(req: SegmentRequest, settings: Settings) -> SegmentationSpec:
    if req.unaries is not None and req.image is not None:
        raise InputValidationError(
            "provide either 'unaries' or 'image', not both",
            code="AMBIGUOUS_INPUT",
        )
    if req.unaries is not None:
        unary0 = np.asarray(req.unaries.unary0, dtype=np.float64)
        unary1 = np.asarray(req.unaries.unary1, dtype=np.float64)
        if unary0.ndim != 2 or unary1.ndim != 2:
            raise InputValidationError(
                "unary arrays must be 2-D",
                code="UNARY_SHAPE_MISMATCH",
                details={"unary0_ndim": unary0.ndim, "unary1_ndim": unary1.ndim},
            )
        if unary0.shape != unary1.shape:
            raise InputValidationError(
                f"unary0 shape {unary0.shape} != unary1 shape {unary1.shape}",
                code="UNARY_SHAPE_MISMATCH",
            )
        height, width = int(unary0.shape[0]), int(unary0.shape[1])
    elif req.image is not None:
        unary0, unary1 = _unaries_from_image(req)
        height, width = int(unary0.shape[0]), int(unary0.shape[1])
    else:
        raise InputValidationError(
            "request must contain 'unaries' or 'image'",
            code="MISSING_INPUT",
        )

    pairwise = req.pairwise.to_spec()
    seeds = [Seed(row=s.row, col=s.col, label=s.label) for s in req.seeds]
    return build_spec(
        height=height, width=width,
        unary0=unary0, unary1=unary1,
        pairwise=pairwise, seeds=seeds, settings=settings,
    )
