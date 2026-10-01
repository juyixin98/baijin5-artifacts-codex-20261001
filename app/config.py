"""Application configuration loaded exclusively from the local environment.

No production accounts or external services are involved; every setting has a
local default so the service starts with zero configuration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
    """Minimal ``.env`` loader (KEY=VALUE, ``#`` comments) — no third party."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        # Explicit environment variables win over the file.
        os.environ.setdefault(key, value)


_load_dotenv()


def _path(key: str, default: str) -> Path:
    raw = os.environ.get(key, default)
    return Path(raw) if os.path.isabs(raw) else REPO_ROOT / raw


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_level: str
    log_file: Path | None
    host: str
    port: int

    @property
    def effective_bandwidth_floor(self) -> float:
        """Minimum bandwidth (running-variable units) accepted below which a
        fit is flagged as non-identifiable."""
        return 1e-12


def get_settings() -> Settings:
    log_file_raw = os.environ.get("RD_LOG_FILE", "logs/rd.jsonl").strip()
    return Settings(
        db_path=_path("RD_DB_PATH", "data/rd.db"),
        log_level=os.environ.get("RD_LOG_LEVEL", "INFO").upper(),
        log_file=_path("", log_file_raw) if log_file_raw else None,
        host=os.environ.get("RD_HOST", "127.0.0.1"),
        port=int(os.environ.get("RD_PORT", "8000")),
    )


settings = get_settings()
