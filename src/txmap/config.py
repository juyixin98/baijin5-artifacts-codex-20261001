"""Configuration loaded from environment with safe local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = REPO_ROOT / "data" / "fixtures" / "transcripts.json"
DEFAULT_DB = REPO_ROOT / "data" / "txmap.sqlite3"


@dataclass(frozen=True)
class Settings:
    fixture_path: Path
    db_path: Path
    log_level: str
    db_echo: bool

    @staticmethod
    def from_env() -> "Settings":
        return Settings(
            fixture_path=Path(os.getenv("TXMAP_FIXTURE", str(DEFAULT_FIXTURE))),
            db_path=Path(os.getenv("TXMAP_DB", str(DEFAULT_DB))),
            log_level=os.getenv("TXMAP_LOG_LEVEL", "INFO").upper(),
            db_echo=os.getenv("TXMAP_DB_ECHO", "0") == "1",
        )


SETTINGS = Settings.from_env()
