"""Application configuration.

All settings can be overridden with ``DIGEST_<NAME>`` environment variables,
e.g. ``DIGEST_DB_PATH=/tmp/x.db`` (used by the test layer).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIGEST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Rule-based Enzymatic Digest Service"
    app_version: str = "1.0.0"

    # Storage / diagnostics
    db_path: str = "data/digest.db"
    log_dir: str = "logs"
    log_level: str = "INFO"
    log_to_file: bool = True

    # Validation limits
    max_sequence_length: int = 10_000
    max_missed_cleavages: int = 10
    max_charge: int = 6
    # Safety bound for variable-modification combinatorial explosion.
    max_modification_forms: int = 1024

    # Mass presentation
    mass_decimals: int = 6

    def ensure_dirs(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
