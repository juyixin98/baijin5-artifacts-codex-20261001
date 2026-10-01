"""Configuration loaded exclusively from environment variables.

No secrets are needed for this local synthetic service; the module exists so
deployment parameters (exact-enumeration budget, DB path, Monte Carlo draws)
are explicit and overridable rather than scattered as magic numbers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = float(raw)
    if not 0.0 < value < 1.0:
        raise ValueError(f"{name} must lie strictly in (0, 1), got {value!r}")
    return value


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Frozen so configuration is immutable per process."""

    db_path: Path
    exact_budget: int  # max full randomization-set size before approximating
    crossing_budget: int  # max pairwise assignment crossings for exact PROB inversion
    mc_draws: int  # Monte Carlo draws for approximate p-values
    mc_seed: int  # base RNG seed (mixed with request id for reproducibility)
    inversion_grid: int  # initial scan resolution for the inversion kernel
    inversion_refine: int  # bisection refinement iterations per boundary
    version: str = "1.0.0"

    @staticmethod
    def from_env() -> "Settings":
        default_db = Path(__file__).resolve().parent.parent / "data" / "service.db"
        return Settings(
            db_path=Path(os.environ.get("PAIRTEST_DB_PATH", str(default_db))),
            exact_budget=_int_env("PAIRTEST_EXACT_BUDGET", 65_536),
            crossing_budget=_int_env("PAIRTEST_CROSSING_BUDGET", 2_000_000),
            mc_draws=_int_env("PAIRTEST_MC_DRAWS", 10_000),
            mc_seed=_int_env("PAIRTEST_MC_SEED", 20260927),
            inversion_grid=_int_env("PAIRTEST_INVERSION_GRID", 4096),
            inversion_refine=_int_env("PAIRTEST_INVERSION_REFINE", 40),
        )


SETTINGS = Settings.from_env()
