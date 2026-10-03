"""Runtime configuration.

Values come from environment variables with repository-local defaults.
No secrets are involved; the service only reads local ICC profiles.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    profile_dir: Path
    max_image_pixels: int
    default_tile_size: int
    gamut_tolerance: int


def get_settings() -> Settings:
    """Build settings from the environment (read on every call, test-friendly)."""
    return Settings(
        profile_dir=Path(os.environ.get("ICCCONV_PROFILE_DIR", _REPO_ROOT / "profiles")),
        max_image_pixels=int(os.environ.get("ICCCONV_MAX_IMAGE_PIXELS", "40000000")),
        default_tile_size=int(os.environ.get("ICCCONV_DEFAULT_TILE_SIZE", "512")),
        gamut_tolerance=int(os.environ.get("ICCCONV_GAMUT_TOLERANCE", "3")),
    )
