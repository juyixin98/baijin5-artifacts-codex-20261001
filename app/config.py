"""Runtime configuration, overridable via environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Service-level knobs. Defaults are chosen for local development."""

    tile_size: int = _int_env("SKEL_TILE_SIZE", 64)
    max_rounds: int = _int_env("SKEL_MAX_ROUNDS", 10_000)
    tile_workers: int = _int_env("SKEL_TILE_WORKERS", 1)
    log_level: str = os.environ.get("SKEL_LOG_LEVEL", "INFO")


settings = Settings()
