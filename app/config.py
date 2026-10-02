"""Configuration layer.

All runtime-tunable settings live here, sourced from environment variables
with the ``WATERSHED_`` prefix and falling back to documented defaults.
No other module reads ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # fail fast on a malformed environment
        raise ValueError(f"environment variable {name}={raw!r} is not an integer") from exc


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the segmentation service."""

    # Maximum number of pixels accepted in a single request. Guards the
    # service against unbounded allocations from malformed clients.
    max_image_pixels: int = 4_000_000
    # Default chunk size (pixels popped from the flood heap per progress
    # batch) for chunked jobs. Chunking never changes the result, only the
    # granularity of progress reporting.
    default_chunk_size: int = 4096
    # Maximum number of retained job records (oldest are evicted).
    max_retained_jobs: int = 128
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_image_pixels=_int_env("WATERSHED_MAX_IMAGE_PIXELS", cls.max_image_pixels),
            default_chunk_size=_int_env("WATERSHED_DEFAULT_CHUNK_SIZE", cls.default_chunk_size),
            max_retained_jobs=_int_env("WATERSHED_MAX_RETAINED_JOBS", cls.max_retained_jobs),
            log_level=os.environ.get("WATERSHED_LOG_LEVEL", cls.log_level),
        )


@dataclass(frozen=True)
class Versions:
    """Dependency versions reported by /health and stamped into run logs."""

    python: str
    numpy: str
    scipy: str
    pillow: str
    fastapi: str
    app: str

    def as_dict(self) -> dict[str, str]:
        return {
            "python": self.python,
            "numpy": self.numpy,
            "scipy": self.scipy,
            "pillow": self.pillow,
            "fastapi": self.fastapi,
            "app": self.app,
        }


def collect_versions() -> Versions:
    import platform

    import fastapi
    import numpy
    import PIL
    import scipy

    from app import __version__

    return Versions(
        python=platform.python_version(),
        numpy=numpy.__version__,
        scipy=scipy.__version__,
        pillow=PIL.__version__,
        fastapi=fastapi.__version__,
        app=__version__,
    )


DEFAULT_SETTINGS: Settings = Settings()
