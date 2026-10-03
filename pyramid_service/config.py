"""Service configuration.  Values are plain data so tests can inject limits."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    store_dir: Path
    max_source_pixels: int = 500_000_000
    max_region_pixels: int = 64_000_000

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            store_dir=Path(os.environ.get("PYRAMID_STORE_DIR", "./pyramid_store")),
            max_source_pixels=int(
                os.environ.get("PYRAMID_MAX_SOURCE_PIXELS", 500_000_000)
            ),
            max_region_pixels=int(
                os.environ.get("PYRAMID_MAX_REGION_PIXELS", 64_000_000)
            ),
        )
