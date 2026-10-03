"""Runtime configuration, overridable via environment variables.

All values have local-development defaults; no production accounts or
external services are involved.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_MAX_STATE_BYTES = 64 * 1024 * 1024  # 64 MiB per session
_DEFAULT_MAX_SESSIONS = 128
_DEFAULT_MAX_IR_LENGTH = 2_000_000
_DEFAULT_MAX_BLOCK_SIZE = 65536


@dataclass(frozen=True)
class Settings:
    max_state_bytes: int = _DEFAULT_MAX_STATE_BYTES
    max_sessions: int = _DEFAULT_MAX_SESSIONS
    max_ir_length: int = _DEFAULT_MAX_IR_LENGTH
    max_block_size: int = _DEFAULT_MAX_BLOCK_SIZE
    service_name: str = "partitioned-convolver"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            max_state_bytes=int(os.environ.get("PCV_MAX_STATE_BYTES", _DEFAULT_MAX_STATE_BYTES)),
            max_sessions=int(os.environ.get("PCV_MAX_SESSIONS", _DEFAULT_MAX_SESSIONS)),
            max_ir_length=int(os.environ.get("PCV_MAX_IR_LENGTH", _DEFAULT_MAX_IR_LENGTH)),
            max_block_size=int(os.environ.get("PCV_MAX_BLOCK_SIZE", _DEFAULT_MAX_BLOCK_SIZE)),
        )
