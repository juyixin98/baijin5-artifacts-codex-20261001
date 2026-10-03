"""Service configuration. Values can be overridden via SOSFILT_* env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None else default


@dataclass(frozen=True)
class Settings:
    """Hard resource limits and numerical policy for the service."""

    max_sections: int = field(default_factory=lambda: _int_env("SOSFILT_MAX_SECTIONS", 32))
    max_channels: int = field(default_factory=lambda: _int_env("SOSFILT_MAX_CHANNELS", 8))
    max_block_samples: int = field(
        default_factory=lambda: _int_env("SOSFILT_MAX_BLOCK_SAMPLES", 16384)
    )
    max_streams: int = field(default_factory=lambda: _int_env("SOSFILT_MAX_STREAMS", 128))
    # Poles with radius >= this limit are rejected (1.0 = strict BIBO stability;
    # poles exactly on the unit circle are marginally stable and also rejected).
    pole_radius_limit: float = 1.0


def load_settings() -> Settings:
    return Settings()
