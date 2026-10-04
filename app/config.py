"""Runtime configuration.

All values have safe local defaults; everything can be overridden through
environment variables so the service needs no production accounts.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    service_name: str = "edt-service"
    # Rasters above this many cells are processed through the tiled path.
    tile_threshold_cells: int = 1_000_000
    # Target number of cells per tile (tiles are whole rows x column bands).
    tile_target_cells: int = 250_000
    # Maximum input edge / total cell count accepted by the HTTP layer.
    max_edge_px: int = 20_000
    max_total_cells: int = 100_000_000
    # Minimum accepted pixel spacing (guards against zero/negative scale).
    min_spacing: float = 1e-9
    max_spacing: float = 1e9
    # Distance values above this are reported as "saturated" in summaries.
    distance_warn_ratio: float = 1e12
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            tile_threshold_cells=_env_int("EDT_TILE_THRESHOLD", 1_000_000),
            tile_target_cells=_env_int("EDT_TILE_TARGET", 250_000),
            max_edge_px=_env_int("EDT_MAX_EDGE", 20_000),
            max_total_cells=_env_int("EDT_MAX_CELLS", 100_000_000),
            min_spacing=_env_float("EDT_MIN_SPACING", 1e-9),
            max_spacing=_env_float("EDT_MAX_SPACING", 1e9),
            log_level=os.environ.get("EDT_LOG_LEVEL", "INFO"),
        )


settings = Settings.from_env()
