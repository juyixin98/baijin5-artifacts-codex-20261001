"""Runtime configuration.

Values come from environment variables with safe local defaults; no
production accounts or external services are involved.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.version import __version__

#: Supported analysis windows (fixed set, see autocorr.apply_window).
SUPPORTED_WINDOWS = ("hann", "hamming", "rect")

#: Hard numerical limits shared by the pipeline and the API validators.
MAX_FRAME_SIZE = 8192
MAX_ORDER = 256
MAX_SAMPLES = 1_000_000


@dataclass(frozen=True)
class Settings:
    """Process-level settings (immutable once created)."""

    app_version: str = __version__
    default_frame_size: int = 256
    default_order: int = 10
    default_window: str = "hann"
    #: relative reconstruction error below this is treated as lossless
    lossless_rel_tol: float = 1e-9
    #: |reflection coefficient| at or above this is reported unstable
    stability_margin: float = 1.0 - 1e-12
    log_level: str = "INFO"


def load_settings() -> Settings:
    """Build settings from LPC_* environment variables."""
    return Settings(
        default_frame_size=int(os.environ.get("LPC_FRAME_SIZE", "256")),
        default_order=int(os.environ.get("LPC_ORDER", "10")),
        default_window=os.environ.get("LPC_WINDOW", "hann"),
        lossless_rel_tol=float(os.environ.get("LPC_LOSSLESS_REL_TOL", "1e-9")),
        log_level=os.environ.get("LPC_LOG_LEVEL", "INFO"),
    )
