"""Environment-driven configuration.

Everything that may differ between local runs lives here and is read from
environment variables once at process start. No path or threshold is
hard-coded inside business logic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Bumped whenever the persisted kernel state format changes.
KERNEL_VERSION = "1.0.0"
ALGORITHM_NAME = "vertical-tidset-prefix-dfs"
# One budget unit = one DFS node entered (one closure test performed).
BUDGET_UNIT = "dfs_node_visit"

# Cap the total enumeration a single unbounded call may perform. The HTTP API
# always passes an explicit per-call budget; this only guards direct kernel use.
HARD_ENUMERATION_CEILING = 2_000_000


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    log_level: str
    default_budget: int
    max_transactions: int
    max_items_per_transaction: int
    max_item_length: int
    max_corpus_name_length: int
    max_advance_budget: int

    @property
    def kernel_version(self) -> str:
        return KERNEL_VERSION


def load_settings() -> Settings:
    """Read settings from the process environment.

    Raises:
        ValueError: if an integer environment variable is malformed. Callers
            at process start let this abort boot loudly rather than silently
            running with a guessed value.
    """
    return Settings(
        db_path=Path(os.environ.get("CFIM_DB_PATH", "data/cfim.db")),
        log_level=os.environ.get("CFIM_LOG_LEVEL", "INFO").upper(),
        default_budget=_env_int("CFIM_DEFAULT_BUDGET", 1000),
        max_transactions=_env_int("CFIM_MAX_TRANSACTIONS", 10_000),
        max_items_per_transaction=_env_int("CFIM_MAX_ITEMS_PER_TX", 256),
        max_item_length=_env_int("CFIM_MAX_ITEM_LENGTH", 128),
        max_corpus_name_length=_env_int("CFIM_MAX_NAME_LENGTH", 200),
        max_advance_budget=_env_int("CFIM_MAX_ADVANCE_BUDGET", 100_000),
    )
