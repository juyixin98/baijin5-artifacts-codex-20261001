"""Request/response schemas for the validation interface."""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from ..contract.enums import AlphaMode, RenderingIntent
from ..errors import ContractViolationError


class ProfileRef(BaseModel):
    """Exactly one way of addressing a profile must be given.

    ``embedded: true`` means "use the profile embedded in the image";
    there is deliberately no 'auto' option - nothing is ever guessed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    icc_b64: str | None = None
    embedded: bool = False

    @model_validator(mode="after")
    def _exactly_one(self) -> "ProfileRef":
        chosen = sum([self.name is not None, self.icc_b64 is not None, self.embedded])
        if chosen != 1:
            raise ValueError(
                "exactly one of 'name', 'icc_b64' or 'embedded: true' must be given"
            )
        return self

    def decode_inline(self) -> bytes:
        assert self.icc_b64 is not None
        try:
            return base64.b64decode(self.icc_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ContractViolationError(
                "icc_b64 is not valid base64", detail={"error": str(exc)[:120]}
            ) from exc


class ConvertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_b64: str
    image_format: Literal["png", "tiff"] = "png"
    source: ProfileRef
    target: ProfileRef
    intent: RenderingIntent = RenderingIntent.PERCEPTUAL
    black_point_compensation: bool = False
    alpha_mode_in: AlphaMode = AlphaMode.STRAIGHT
    alpha_mode_out: AlphaMode | None = None
    check_gamut: bool = False
    tile_size: int | None = None
    output_format: Literal["png", "tiff"] | None = None

    @model_validator(mode="after")
    def _target_not_embedded(self) -> "ConvertRequest":
        if self.target.embedded:
            raise ValueError("target profile cannot be 'embedded'")
        return self


class ValidateProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    icc_b64: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ValidateProfileRequest":
        if (self.name is None) == (self.icc_b64 is None):
            raise ValueError("exactly one of 'name' or 'icc_b64' must be given")
        return self


class ErrorBody(BaseModel):
    status: Literal["rejected", "failed"]
    category: str
    message: str
    request_id: str
    diagnostics: list[dict[str, Any]] = []
