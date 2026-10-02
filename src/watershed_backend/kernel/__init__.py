"""Numerical kernel package."""

from .watershed import (
    BOUNDARY_LABEL,
    NEIGHBOR_OFFSETS,
    UNREACHED_LABEL,
    FloodStats,
    WatershedResult,
    flood_watershed,
)

__all__ = [
    "BOUNDARY_LABEL",
    "NEIGHBOR_OFFSETS",
    "UNREACHED_LABEL",
    "FloodStats",
    "WatershedResult",
    "flood_watershed",
]
