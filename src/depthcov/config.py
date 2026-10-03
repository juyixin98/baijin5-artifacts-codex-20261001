"""Runtime configuration with environment overrides and strict validation."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .coverage import UNION_PER_QUERY, VALID_POLICIES


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"environment {name}={raw!r} is not an integer") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    min_mapq: int = 20
    dedup_policy: str = UNION_PER_QUERY
    reject_flagged_duplicates: bool = True
    external_sort_chunk_size: int = 50_000
    use_external_sort_threshold: int = 100_000
    db_path: str = ":memory:"
    log_level: str = "INFO"

    @staticmethod
    def from_env() -> "Settings":
        policy = os.environ.get("DEPTHCOV_DEDUP_POLICY", UNION_PER_QUERY)
        if policy not in VALID_POLICIES:
            raise ValueError(
                f"DEPTHCOV_DEDUP_POLICY={policy!r} invalid; "
                f"choose one of {sorted(VALID_POLICIES)}"
            )
        mapq = _env_int("DEPTHCOV_MIN_MAPQ", 20)
        if not 0 <= mapq <= 255:
            raise ValueError("DEPTHCOV_MIN_MAPQ must be in [0, 255]")
        chunk = _env_int("DEPTHCOV_SORT_CHUNK", 50_000)
        if chunk <= 0:
            raise ValueError("DEPTHCOV_SORT_CHUNK must be positive")
        return Settings(
            min_mapq=mapq,
            dedup_policy=policy,
            reject_flagged_duplicates=_env_bool(
                "DEPTHCOV_REJECT_FLAGGED_DUPES", True
            ),
            external_sort_chunk_size=chunk,
            use_external_sort_threshold=_env_int(
                "DEPTHCOV_SORT_THRESHOLD", 100_000
            ),
            db_path=os.environ.get("DEPTHCOV_DB", ":memory:"),
            log_level=os.environ.get("DEPTHCOV_LOG_LEVEL", "INFO"),
        )
