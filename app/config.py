"""Application configuration.

All settings are environment-driven (12-factor style). Defaults are chosen so
that a clean checkout runs end-to-end without any external account.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: string, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Exact sign-flip enumeration is allowed while 2**n_pairs <= this budget.
    exact_budget_flips: int = _env_int("RCT_EXACT_BUDGET_FLIPS", 100_000)
    # Default number of Monte-Carlo sign-flip draws.
    mc_default_draws: int = _env_int("RCT_MC_DRAWS", 10_000)
    # Monte-Carlo confidence level for the reported binomial error band on p.
    mc_error_confidence: float = _env_float("RCT_MC_ERROR_CONFIDENCE", 0.95)
    # Default inversion grid for approximate confidence sets.
    grid_half_width: float = _env_float("RCT_GRID_HALF_WIDTH", 10.0)
    grid_points: int = _env_int("RCT_GRID_POINTS", 4_001)
    # Storage / logging.
    database_path: str = os.environ.get("RCT_DB_PATH", "data/rct.sqlite3")
    log_path: str = os.environ.get("RCT_LOG_PATH", "data/app.log")
    log_level: str = os.environ.get("RCT_LOG_LEVEL", "INFO")
    # Service identity surfaced in diagnostics.
    service_name: str = "paired-rct-inference"

    @property
    def version(self) -> str:
        from app import __version__

        return __version__


settings = Settings()
