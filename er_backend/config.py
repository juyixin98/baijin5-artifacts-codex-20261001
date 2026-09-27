"""Runtime configuration. All values have safe defaults and can be
overridden via environment variables (prefix ER_)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    return os.environ.get(f"ER_{name}", default)


@dataclass(frozen=True)
class Settings:
    # Similarity threshold above which a candidate pair is eligible to merge.
    merge_threshold: float = float(_env("MERGE_THRESHOLD", "0.75"))
    # Resource budgets.
    max_records: int = int(_env("MAX_RECORDS", "5000"))
    max_candidates: int = int(_env("MAX_CANDIDATES", "20000"))
    # Blocking: ignore tokens appearing in more than this fraction of records.
    blocking_df_cap: float = float(_env("BLOCKING_DF_CAP", "0.5"))
    # Storage.
    db_path: str = _env("DB_PATH", "er_backend.sqlite3")
    # Similarity feature weights (must sum to 1.0 for the weighted branch).
    weight_jaccard: float = 0.6
    weight_edit: float = 0.4
    # Alias / exact-name scores.
    exact_name_score: float = 1.0
    alias_score: float = 0.9
    shared_id_score: float = 1.0

    def validate(self) -> None:
        if not 0.0 < self.merge_threshold <= 1.0:
            raise ValueError("merge_threshold must be in (0, 1]")
        if abs(self.weight_jaccard + self.weight_edit - 1.0) > 1e-9:
            raise ValueError("similarity weights must sum to 1.0")


DEFAULT_SETTINGS = Settings()
