"""Independent runtime configuration.

All knobs are read from environment variables so the service, tests and the
demo script can be configured without touching source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    default_budget_nodes: int
    max_budget_nodes: int
    log_level: str

    @staticmethod
    def _positive_int(raw: str, name: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
        if value < 1:
            raise ValueError(f"{name} must be >= 1, got {value}")
        return value


def get_settings() -> Settings:
    return Settings(
        database_path=os.environ.get("FIM_DB_PATH", os.path.join(os.getcwd(), "fim.db")),
        default_budget_nodes=Settings._positive_int(
            os.environ.get("FIM_DEFAULT_BUDGET_NODES", "10000"),
            "FIM_DEFAULT_BUDGET_NODES",
        ),
        max_budget_nodes=Settings._positive_int(
            os.environ.get("FIM_MAX_BUDGET_NODES", "1000000"),
            "FIM_MAX_BUDGET_NODES",
        ),
        log_level=os.environ.get("FIM_LOG_LEVEL", "INFO").upper(),
    )
