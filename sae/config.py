"""Runtime configuration.

All settings come from environment variables so nothing secret is ever
hardcoded.  ``SAE_MASTER_KEY`` is a 64-char hex string (32 bytes); the
service refuses to start without it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_MASTER_KEY = "SAE_MASTER_KEY"
ENV_DB_PATH = "SAE_DB_PATH"
ENV_MAX_CHUNK = "SAE_MAX_CHUNK_BYTES"
ENV_MAX_CHUNKS = "SAE_MAX_CHUNKS"

DEFAULT_DB_PATH = "sae.db"
DEFAULT_MAX_CHUNK_BYTES = 1 << 20  # 1 MiB per chunk
DEFAULT_MAX_CHUNKS = 1 << 16


@dataclass(frozen=True)
class Settings:
    master_key: bytes
    db_path: str = DEFAULT_DB_PATH
    max_chunk_bytes: int = DEFAULT_MAX_CHUNK_BYTES
    max_chunks: int = DEFAULT_MAX_CHUNKS

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = os.environ if env is None else env
        raw = env.get(ENV_MASTER_KEY, "")
        if not raw:
            raise RuntimeError(
                f"{ENV_MASTER_KEY} is not set; expected 64 hex chars (32 bytes)"
            )
        try:
            master_key = bytes.fromhex(raw)
        except ValueError as exc:
            raise RuntimeError(f"{ENV_MASTER_KEY} is not valid hex") from exc
        if len(master_key) != 32:
            raise RuntimeError(f"{ENV_MASTER_KEY} must decode to 32 bytes")
        return cls(
            master_key=master_key,
            db_path=env.get(ENV_DB_PATH, DEFAULT_DB_PATH),
            max_chunk_bytes=int(env.get(ENV_MAX_CHUNK, DEFAULT_MAX_CHUNK_BYTES)),
            max_chunks=int(env.get(ENV_MAX_CHUNKS, DEFAULT_MAX_CHUNKS)),
        )
