"""Runtime configuration, loaded from config/default.json when present.

Configuration lives outside the package and outside the tests so that
algorithm code, test fixtures, and deployment settings evolve independently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.json"


@dataclass(frozen=True)
class Settings:
    """Service-level knobs; the metric, step pattern, and window semantics
    are contract-fixed and deliberately absent here."""

    sakoe_chiba_radius: int = 8
    smoothing_window: int = 5
    max_sequence_length: int = 100_000


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from JSON, falling back to defaults for missing keys."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return Settings()
    overrides = json.loads(config_path.read_text(encoding="utf-8"))
    known = {k: v for k, v in overrides.items() if k in Settings.__dataclass_fields__}
    return Settings(**known)
