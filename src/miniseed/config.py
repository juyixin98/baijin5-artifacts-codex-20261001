"""Configuration layer for the minimizer seed index service.

Values are deliberately explicit (no magic numbers in algorithm code) and can
be overridden through environment variables for local runs / tests.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
FIXTURE_DIR = PROJECT_ROOT / "fixtures"
LOG_DIR = PROJECT_ROOT / "logs"

# Fixed knucleotide hashing parameters (canonical two-bit encoding).
DEFAULT_K = 9
DEFAULT_W = 5
# Low-complexity / repetitive minimizer outbreak guard: at most this many
# reference offsets may be stored per (run_id, minimizer) bucket.
DEFAULT_MAX_BUCKET_SIZE = 200
# Above this number of candidate hits a query result is flagged
# ``over_capacity`` (the caller is expected to narrow the query).
DEFAULT_MAX_CANDIDATES = 500
# Query reads shorter than k + w - 1 cannot produce a single minimizer window.
MIN_READ_LENGTH_FLOOR = 1

# Hash seed (FNV offset basis style) -- fixed so results are reproducible.
HASH_SEED = 0xCBF29CE484222325
HASH_MOD = 1 << 63  # keep values in non-negative signed 64-bit range for JSON


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    """Immutable service settings (see module level constants for defaults)."""

    k: int = DEFAULT_K
    w: int = DEFAULT_W
    max_bucket_size: int = DEFAULT_MAX_BUCKET_SIZE
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    db_path: Path = DATA_DIR / "miniseed.db"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            k=_env_int("MINISEED_K", DEFAULT_K),
            w=_env_int("MINISEED_W", DEFAULT_W),
            max_bucket_size=_env_int(
                "MINISEED_MAX_BUCKET", DEFAULT_MAX_BUCKET_SIZE
            ),
            max_candidates=_env_int(
                "MINISEED_MAX_CANDIDATES", DEFAULT_MAX_CANDIDATES
            ),
            db_path=Path(
                os.environ.get("MINISEED_DB", str(DATA_DIR / "miniseed.db"))
            ),
        )
