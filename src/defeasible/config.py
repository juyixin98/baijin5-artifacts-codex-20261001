"""Independent configuration loaded from environment variables.

No third-party settings dependency is required; every value has a safe
local default so the service runs from a clean checkout with zero setup.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .engine import EngineLimits


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"environment variable {name} must be an integer, got {raw!r}") from e
    if value <= 0:
        raise ValueError(f"environment variable {name} must be positive, got {value}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    db_path: str = "data/defeasible.db"
    log_path: str = "logs/runs.jsonl"
    host: str = "127.0.0.1"
    port: int = 8000
    max_ground_rules: int = 100_000
    max_chains: int = 1_000
    max_rounds: int = 1_000

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            db_path=os.environ.get("DEFEASIBLE_DB_PATH", "data/defeasible.db"),
            log_path=os.environ.get("DEFEASIBLE_LOG_PATH", "logs/runs.jsonl"),
            host=os.environ.get("DEFEASIBLE_HOST", "127.0.0.1"),
            port=_env_int("DEFEASIBLE_PORT", 8000),
            max_ground_rules=_env_int("DEFEASIBLE_MAX_GROUND_RULES", 100_000),
            max_chains=_env_int("DEFEASIBLE_MAX_CHAINS", 1_000),
            max_rounds=_env_int("DEFEASIBLE_MAX_ROUNDS", 1_000),
        )

    def engine_limits(self) -> EngineLimits:
        return EngineLimits(
            max_ground_rules=self.max_ground_rules,
            max_chains=self.max_chains,
            max_rounds=self.max_rounds,
        )
