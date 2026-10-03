"""Runtime configuration and capacity limits.

Values are plain constants with environment overrides so a clean checkout
reproduces the documented behaviour without any external service.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # Learning-rate bounds. NLMS is stable for 0 < mu < 2 (exclusive upper
    # bound). LMS stability additionally depends on input power
    # (mu < 2 / lambda_max of the reference autocorrelation); the configured
    # bound is a sanity ceiling, not a convergence guarantee.
    mu_max_lms: float = 1.0
    nlms_mu_upper: float = 2.0  # exclusive
    default_epsilon: float = 1e-8

    # Capacity limits -> resource_exhausted when exceeded.
    max_filter_length: int = 4096
    max_block_length: int = 65536
    max_streams: int = 64
    max_channels_per_stream: int = 16

    log_dir: str = "logs"


def get_settings() -> Settings:
    """Build settings, honouring the LMS_LOG_DIR override at call time."""
    return Settings(log_dir=os.environ.get("LMS_LOG_DIR", "logs"))
