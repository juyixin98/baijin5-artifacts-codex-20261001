"""Image data contract: request/response models and binary-image validation."""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field, model_validator

ERROR_CONTRACT_VIOLATION = "contract_violation"
ERROR_UNKNOWN_SAMPLE = "unknown_sample"
ERROR_ENGINE = "engine_error"


class ThinRequest(BaseModel):
    """Exactly one of ``pixels`` / ``sample`` selects the input image."""

    pixels: list[list[int]] | None = None
    sample: str | None = None
    engine: Literal["full", "tiled"] = "full"
    tile_size: int | None = Field(default=None, ge=1)
    max_rounds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "ThinRequest":
        if (self.pixels is None) == (self.sample is None):
            raise ValueError("provide exactly one of 'pixels' or 'sample'")
        return self


def validate_binary_image(pixels: list[list[int]], max_dim: int) -> np.ndarray:
    """Enforce the image contract; raise ValueError with a precise reason."""
    if not pixels or not pixels[0]:
        raise ValueError("image must be a non-empty 2D array")
    width = len(pixels[0])
    if any(len(row) != width for row in pixels):
        raise ValueError("image rows must all have the same length")
    height = len(pixels)
    if height > max_dim or width > max_dim:
        raise ValueError(f"image dimensions exceed the {max_dim}px limit")
    values = {v for row in pixels for v in row}
    if not values <= {0, 1}:
        raise ValueError(f"image must be binary 0/1, got values {sorted(values)}")
    return np.asarray(pixels, dtype=np.uint8)


def image_to_payload(image: np.ndarray) -> list[list[int]]:
    return np.asarray(image, dtype=np.uint8).tolist()
