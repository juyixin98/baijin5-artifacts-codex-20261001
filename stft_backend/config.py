"""Service settings and processing-location metadata.

Settings are environment driven (prefix ``STFT_``) so the same image can run
with different defaults without code changes.
"""

from __future__ import annotations

import os
import platform
import socket
from dataclasses import dataclass

SERVICE_NAME = "stft-backend"
SERVICE_VERSION = "1.0.0"
API_V1 = "/v1"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None and raw != "" else default


@dataclass(frozen=True)
class Settings:
    default_nperseg: int
    default_hop: int
    default_window: str
    log_level: str
    max_signal_samples: int
    max_frames_per_session: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            default_nperseg=_env_int("STFT_DEFAULT_NPERSEG", 256),
            default_hop=_env_int("STFT_DEFAULT_HOP", 128),
            default_window=os.getenv("STFT_DEFAULT_WINDOW", "hann"),
            log_level=os.getenv("STFT_LOG_LEVEL", "INFO").upper(),
            max_signal_samples=_env_int("STFT_MAX_SIGNAL_SAMPLES", 1_000_000),
            max_frames_per_session=_env_int("STFT_MAX_FRAMES_PER_SESSION", 100_000),
        )


def processing_location() -> dict:
    """Describe where processing happens (host / runtime / module version)."""

    return {
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "host": socket.gethostname(),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
