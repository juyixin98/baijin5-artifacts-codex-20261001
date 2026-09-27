"""Configuration layer.

All tunables live here (and map to environment variables for the API
service); nothing about execution limits is hard-coded in the engine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EngineConfig:
    #: Default firing bound for Engine.run() when none is given.
    default_max_cycles: int = 100
    #: Hard ceiling; callers cannot request an unbounded run.
    max_cycles_limit: int = 100_000


def get_database_url() -> str:
    return os.environ.get("RETE_DB_PATH", "rete_evidence.db")


def get_service_config() -> dict:
    """Service-layer configuration read from the environment with defaults."""
    return {
        "db_path": os.environ.get("RETE_DB_PATH", "rete_evidence.db"),
        "log_level": os.environ.get("RETE_LOG_LEVEL", "INFO"),
        "default_max_cycles": int(os.environ.get("RETE_DEFAULT_MAX_CYCLES", "100")),
        "max_cycles_limit": int(os.environ.get("RETE_MAX_CYCLES_LIMIT", "100000")),
    }
