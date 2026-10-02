"""Runtime configuration.

Values may be overridden with environment variables prefixed ``EDT_``,
e.g. ``EDT_TILE_SIZE=256``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Service-level knobs.

    tile_size:
        Side length of the square tiles used by the tiled executor.
    direct_max_pixels:
        Rasters with at most this many pixels are computed directly with
        the kernel; larger rasters go through the tiled executor.
    max_grid_pixels:
        Hard refusal limit for a single request (payload safety).
    """

    tile_size: int = field(default_factory=lambda: _env_int("EDT_TILE_SIZE", 512))
    direct_max_pixels: int = field(
        default_factory=lambda: _env_int("EDT_DIRECT_MAX_PIXELS", 4_000_000)
    )
    max_grid_pixels: int = field(
        default_factory=lambda: _env_int("EDT_MAX_GRID_PIXELS", 100_000_000)
    )


def get_settings() -> Settings:
    return Settings()
