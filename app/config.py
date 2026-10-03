"""Configuration layer.

All tunables live here so tests, the API and the validation scripts share one
source of truth. Values can be overridden through environment variables with
the ``TFS_`` prefix (e.g. ``TFS_WORKSPACE_DIR=/tmp/ws``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(f"TFS_{name}", default)


@dataclass(frozen=True)
class Settings:
    """Runtime settings for the filtering service."""

    # Root directory for job workspaces (manifests, checkpoints, outputs).
    workspace_dir: Path = field(
        default_factory=lambda: Path(_env("WORKSPACE_DIR", "workspace"))
    )
    # Root directory for structured run logs.
    log_dir: Path = field(default_factory=lambda: Path(_env("LOG_DIR", "logs")))
    # Numerical tolerance used when comparing tiled output against references.
    # The pipeline is float64 end to end; tiled vs direct is the same additions
    # in the same order per output pixel, so agreement is usually exact, but we
    # keep a small tolerance for reference implementations that reorder sums.
    compare_tolerance: float = float(_env("COMPARE_TOLERANCE", "1e-9"))
    # Guard rail: refuse images larger than this many pixels (DoS protection
    # for the API; local fixtures are far below this).
    max_image_pixels: int = int(_env("MAX_IMAGE_PIXELS", str(200_000_000)))
    # Guard rail: refuse kernels larger than this per axis.
    max_kernel_size: int = int(_env("MAX_KERNEL_SIZE", "1024"))
    # Default tile size used when a request does not specify one.
    default_tile_hw: tuple[int, int] = (512, 512)


def get_settings() -> Settings:
    return Settings()
