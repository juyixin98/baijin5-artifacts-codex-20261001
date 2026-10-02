"""Image/grid data contract.

Defines what a valid request looks like and converts the JSON wire form
into validated NumPy arrays. All boundary validation lives here and fails
fast with categorized errors; the numeric layers trust the contract.

Wire format (JSON):
    grid:    nested lists of 0/1, rectangular, non-empty; 1 = source.
    spacing: [dy, dx], both positive and finite; default [1.0, 1.0].

Result encoding (JSON):
    distances: nested floats; ``null`` encodes +inf (no source exists).
    labels:    nested ints; flat index ``row * width + col`` of the
               nearest source; ``null`` when no source exists.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from pydantic import BaseModel, Field, field_validator

from .errors import InvalidGridError, InvalidOptionError, InvalidSpacingError

MAX_GRID_DIM = 100_000


class EdtRequest(BaseModel):
    """Validated request body for the EDT endpoint."""

    grid: list[list[int]] = Field(..., description="Binary raster, 1 = source")
    spacing: tuple[float, float] = Field(
        default=(1.0, 1.0), description="(dy, dx) pixel pitch, positive"
    )
    tile_size: Optional[int] = Field(
        default=None,
        description="Force tiled execution with this tile side length",
    )
    request_id: Optional[str] = Field(
        default=None, description="Client-supplied correlation id"
    )

    @field_validator("grid", mode="before")
    @classmethod
    def _grid_well_formed(cls, grid: object) -> object:
        # mode="before": inspect the raw wire value so that e.g. booleans
        # and fractional floats are rejected instead of silently coerced.
        if not isinstance(grid, list) or len(grid) == 0:
            raise InvalidGridError("grid must be a non-empty list of rows")
        if len(grid) > MAX_GRID_DIM:
            raise InvalidGridError(f"grid has more than {MAX_GRID_DIM} rows")
        first = grid[0]
        if not isinstance(first, list) or len(first) == 0:
            raise InvalidGridError("grid rows must be non-empty lists")
        width = len(first)
        if width > MAX_GRID_DIM:
            raise InvalidGridError(f"grid has more than {MAX_GRID_DIM} columns")
        for i, row in enumerate(grid):
            if not isinstance(row, list) or len(row) != width:
                raise InvalidGridError(
                    f"grid is ragged: row 0 has {width} cells but row {i} "
                    f"has {len(row) if isinstance(row, list) else 'non-list'}"
                )
            for j, value in enumerate(row):
                if isinstance(value, bool) or not isinstance(value, int):
                    raise InvalidGridError(
                        f"grid[{i}][{j}]={value!r} is not an integer 0/1"
                    )
                if value not in (0, 1):
                    raise InvalidGridError(
                        f"grid[{i}][{j}]={value} is not binary; expected 0 or 1"
                    )
        return grid

    @field_validator("spacing")
    @classmethod
    def _spacing_well_formed(
        cls, spacing: tuple[float, float]
    ) -> tuple[float, float]:
        dy, dx = spacing
        for name, value in (("dy", dy), ("dx", dx)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise InvalidSpacingError(f"spacing {name}={value!r} is not a number")
            if not math.isfinite(value) or value <= 0:
                raise InvalidSpacingError(
                    f"spacing {name}={value} must be positive and finite"
                )
        return (float(dy), float(dx))

    @field_validator("tile_size")
    @classmethod
    def _tile_size_well_formed(cls, tile_size: Optional[int]) -> Optional[int]:
        if tile_size is not None and tile_size <= 0:
            raise InvalidOptionError("tile_size must be a positive integer")
        return tile_size


def grid_to_mask(grid: list[list[int]]) -> np.ndarray:
    """Convert a validated wire grid to a boolean mask (True = source)."""
    return np.asarray(grid, dtype=bool)


def encode_distances(dist: np.ndarray) -> list[list[Optional[float]]]:
    """JSON-safe distances: +inf (no source) becomes null."""
    return [
        [None if math.isinf(v) else float(v) for v in row] for row in dist.tolist()
    ]


def encode_labels(labels: np.ndarray) -> list[list[Optional[int]]]:
    """JSON-safe labels: -1 (no source) becomes null."""
    return [[None if v < 0 else int(v) for v in row] for row in labels.tolist()]
