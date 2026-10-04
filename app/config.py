"""Environment-driven configuration with validated defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .core.errors import LimitError


def _env_int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise LimitError(f"{name} must be an integer", value=raw) from exc
    if value <= 0:
        raise LimitError(f"{name} must be positive", value=value)
    return value


@dataclass(frozen=True)
class Settings:
    db_path: str
    staging_dir: str
    release_dir: str
    key_file: str | None
    backend: str
    max_segment_bytes: int
    max_total_bytes: int
    host: str
    port: int
    log_level: str

    @staticmethod
    def from_env(env: dict[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        return Settings(
            db_path=env.get("SSEA_DB_PATH", "./data/ssea.sqlite3"),
            staging_dir=env.get("SSEA_STAGING_DIR", "./data/staging"),
            release_dir=env.get("SSEA_RELEASE_DIR", "./data/released"),
            key_file=env.get("SSEA_KEY_FILE") or None,
            backend=env.get("SSEA_AEAD_BACKEND", "cryptography"),
            max_segment_bytes=_env_int(env, "SSEA_MAX_SEGMENT_BYTES", 1 << 20),
            max_total_bytes=_env_int(env, "SSEA_MAX_TOTAL_BYTES", 64 << 20),
            host=env.get("SSEA_HOST", "127.0.0.1"),
            port=_env_int(env, "SSEA_PORT", 8080),
            log_level=env.get("SSEA_LOG_LEVEL", "INFO"),
        )


def ensure_base_dirs(settings: Settings) -> None:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.staging_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.release_dir).mkdir(parents=True, exist_ok=True)
