"""Runtime configuration.  Everything else imports settings from here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    profile_dir: Path = PACKAGE_ROOT / "profiles"
    registry_path: Path = PACKAGE_ROOT / "profiles" / "REGISTRY.json"
    max_pixels: int = 40_000_000
    default_tile_size: int = 512
    min_tile_size: int = 16
    max_tile_size: int = 4096
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        return cls(
            profile_dir=Path(os.environ.get("CC_PROFILE_DIR", base.profile_dir)),
            registry_path=Path(
                os.environ.get("CC_REGISTRY_PATH", base.registry_path)
            ),
            max_pixels=int(os.environ.get("CC_MAX_PIXELS", base.max_pixels)),
            default_tile_size=int(
                os.environ.get("CC_TILE_SIZE", base.default_tile_size)
            ),
            log_level=os.environ.get("CC_LOG_LEVEL", base.log_level),
        )
