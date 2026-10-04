"""Runtime configuration. Values come from the environment; defaults are
local-only and safe for development."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "var/commit_reveal.db"
    host: str = "127.0.0.1"
    port: int = 8000


def load_settings() -> Settings:
    return Settings(
        db_path=os.environ.get("CRP_DB_PATH", Settings.db_path),
        host=os.environ.get("CRP_HOST", Settings.host),
        port=int(os.environ.get("CRP_PORT", str(Settings.port))),
    )
