"""Application configuration with environment overrides and validation.

Values are validated at startup so a misconfigured deployment fails fast
rather than silently producing wrong estimates.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import scipy


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - exercised via API config tests
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


class ConfigError(ValueError):
    """Raised when runtime configuration is invalid."""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings.

    Attributes
    ----------
    db_path:
        SQLite database file (``:memory:`` is allowed for tests).
    min_obs_per_side:
        Minimum effective observations each side needed to even attempt a
        local linear fit. Below this the run is marked UNIDENTIFIED.
    bootstrap_reps:
        Default wild-bootstrap replications used when the caller does not
        pin one. Kept modest so the synchronous API stays responsive.
    default_alpha:
        Default significance level (two-sided) for confidence intervals.
    log_level:
        Logging threshold name.
    """

    db_path: str
    min_obs_per_side: int
    bootstrap_reps: int
    default_alpha: float
    log_level: str

    @staticmethod
    def from_env() -> "Settings":
        min_obs = _env_int("RD_MIN_OBS", 10)
        reps = _env_int("RD_BOOTSTRAP_REPS", 999)
        alpha = _env_float("RD_DEFAULT_ALPHA", 0.05)
        if min_obs < 3:
            raise ConfigError("RD_MIN_OBS must be >= 3 (need rank for a line)")
        if reps < 0:
            raise ConfigError("RD_BOOTSTRAP_REPS must be >= 0")
        if not 0.0 < alpha < 1.0:
            raise ConfigError("RD_DEFAULT_ALPHA must lie strictly in (0, 1)")
        return Settings(
            db_path=os.getenv("RD_DB_PATH", "data/rd_runs.db"),
            min_obs_per_side=min_obs,
            bootstrap_reps=reps,
            default_alpha=alpha,
            log_level=os.getenv("RD_LOG_LEVEL", "INFO").upper(),
        )

    def versions(self) -> dict[str, str]:
        """Dependency versions, embedded in every run record / log line."""
        import fastapi

        from app import __version__

        return {
            "app": __version__,
            "python": os.sys.version.split()[0],
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "fastapi": fastapi.__version__,
        }


def default_db_path() -> Path:
    return Path("data/rd_runs.db")
