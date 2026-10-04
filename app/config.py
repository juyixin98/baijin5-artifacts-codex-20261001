"""Runtime configuration (environment-overridable, no secrets)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import __version__


@dataclass(frozen=True)
class Settings:
    version: str = __version__
    #: Hard caps to keep requests bounded (local synthetic workloads only).
    max_samples: int = 2_000_000
    max_frames: int = 200_000
    log_level: str = "INFO"


def load_settings() -> Settings:
    return Settings(
        max_samples=int(os.environ.get("STFT_MAX_SAMPLES", "2000000")),
        max_frames=int(os.environ.get("STFT_MAX_FRAMES", "200000")),
        log_level=os.environ.get("STFT_LOG_LEVEL", "INFO"),
    )


settings = load_settings()
