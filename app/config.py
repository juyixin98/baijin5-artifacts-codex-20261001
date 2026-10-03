"""Runtime configuration. All values overridable via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    version: str = "0.1.0"
    default_tile_size: int = 64
    max_rounds: int = 256
    max_image_dim: int = 2048
    halo_width: int = 1  # Zhang-Suen needs a 1-pixel neighbourhood


def load_settings() -> Settings:
    """Build settings from environment, falling back to defaults."""
    return Settings(
        version=os.environ.get("SKELETON_VERSION", Settings.version),
        default_tile_size=int(os.environ.get("SKELETON_TILE_SIZE", Settings.default_tile_size)),
        max_rounds=int(os.environ.get("SKELETON_MAX_ROUNDS", Settings.max_rounds)),
        max_image_dim=int(os.environ.get("SKELETON_MAX_IMAGE_DIM", Settings.max_image_dim)),
    )
