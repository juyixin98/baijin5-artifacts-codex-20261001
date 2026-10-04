"""Runtime configuration from environment variables (prefix CRP_).

Defaults are local-only: a SQLite file under ./data and a loopback bind.
See .env.example for the documented knobs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_PREFIX = "CRP_"


@dataclass(frozen=True)
class Settings:
    database_path: str = "./data/commit_reveal.db"
    host: str = "127.0.0.1"
    port: int = 8529
    default_min_reveals: int = 2
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Settings":
        def get(name: str, default: str) -> str:
            return os.environ.get(ENV_PREFIX + name, default)

        return Settings(
            database_path=get("DATABASE_PATH", Settings.database_path),
            host=get("HOST", Settings.host),
            port=int(get("PORT", str(Settings.port))),
            default_min_reveals=int(
                get("DEFAULT_MIN_REVEALS", str(Settings.default_min_reveals))
            ),
            log_level=get("LOG_LEVEL", Settings.log_level),
        )
