"""Runtime configuration.

Defaults live in config/default.json next to the repository root; any key
can be overridden by a JSON file pointed to by the HAPLO_CONFIG environment
variable, and the provenance DB path can additionally be overridden with
HAPLO_PROVENANCE_DB (used by tests to isolate the store).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.json"


@dataclass(frozen=True)
class Settings:
    # Phred qualities above this are rejected at validation time.
    max_quality: int = 60
    # Blocks with more sites than this are refused: exact MEC enumeration
    # costs 2**(k-1) candidate haplotypes.
    max_enum_sites: int = 20
    # SQLite provenance store.
    provenance_db_path: str = str(REPO_ROOT / "data" / "provenance.db")
    # Directory with synthetic fixture datasets.
    fixtures_dir: str = str(REPO_ROOT / "fixtures")
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    raw: dict = {}
    if DEFAULT_CONFIG_PATH.exists():
        raw = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    override_path = os.environ.get("HAPLO_CONFIG")
    if override_path:
        raw.update(json.loads(Path(override_path).read_text(encoding="utf-8")))
    known = {f for f in Settings.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    settings = Settings(**kwargs, extra={k: v for k, v in raw.items() if k not in known})
    db_override = os.environ.get("HAPLO_PROVENANCE_DB")
    if db_override:
        object.__setattr__(settings, "provenance_db_path", db_override)
    return settings
