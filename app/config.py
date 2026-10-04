"""Process configuration sourced from environment variables with local defaults.

Everything defaults to a fully local, offline setup (SQLite file under
``data/``, logs under ``logs/``, bundled synthetic enzyme table).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None else default


def _base_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass(frozen=True)
class Settings:
    app_version: str
    base_dir: str
    db_path: str
    enzyme_table_path: str
    log_dir: str
    log_level: str
    max_sequence_length: int
    max_missed_cleavages: int
    mass_decimals: int

    @classmethod
    def from_env(cls) -> "Settings":
        base = _base_dir()
        db_path = os.environ.get("DIGEST_DB_PATH", os.path.join(base, "data", "digest.db"))
        enzyme_table_path = os.environ.get(
            "DIGEST_ENZYME_TABLE", os.path.join(base, "data", "enzymes.json")
        )
        log_dir = os.environ.get("DIGEST_LOG_DIR", os.path.join(base, "logs"))
        log_level = os.environ.get("DIGEST_LOG_LEVEL", "INFO")
        from app import __version__

        return cls(
            app_version=__version__,
            base_dir=base,
            db_path=db_path,
            enzyme_table_path=enzyme_table_path,
            log_dir=log_dir,
            log_level=log_level.upper(),
            max_sequence_length=_env_int("DIGEST_MAX_SEQUENCE_LENGTH", 10_000),
            max_missed_cleavages=_env_int("DIGEST_MAX_MISSED_CLEAVAGES", 10),
            mass_decimals=_env_int("DIGEST_MASS_DECIMALS", 6),
        )


settings = Settings.from_env()
