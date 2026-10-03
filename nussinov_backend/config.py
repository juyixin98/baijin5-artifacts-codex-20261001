"""Application configuration loaded from environment variables.

All settings have local defaults so the project runs from a clean checkout
without any external account or service.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "nussinov.db"

DEFAULT_MAX_SEQUENCE_LENGTH = 200
DEFAULT_MAX_STRUCTURES_LIMIT = 100
VALID_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


def _read_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be within [{minimum}, {maximum}], got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    db_path: Path
    max_sequence_length: int
    max_structures_limit: int
    log_level: str


def _build_settings() -> Settings:
    raw_path = os.environ.get("NUSSINOV_DB_PATH", "").strip()
    db_path = Path(raw_path) if raw_path else DEFAULT_DB_PATH

    log_level = os.environ.get("NUSSINOV_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(
            f"NUSSINOV_LOG_LEVEL must be one of {VALID_LOG_LEVELS}, got {log_level!r}"
        )

    return Settings(
        db_path=db_path,
        max_sequence_length=_read_int(
            "NUSSINOV_MAX_SEQUENCE_LENGTH",
            DEFAULT_MAX_SEQUENCE_LENGTH,
            minimum=1,
            maximum=10_000,
        ),
        max_structures_limit=_read_int(
            "NUSSINOV_MAX_STRUCTURES_LIMIT",
            DEFAULT_MAX_STRUCTURES_LIMIT,
            minimum=1,
            maximum=10_000,
        ),
        log_level=log_level,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _build_settings()
