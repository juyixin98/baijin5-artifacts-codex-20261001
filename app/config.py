"""Service configuration.

Loaded from ``config/default.yaml`` (path overridable via ``HAPLO_CONFIG``);
the SQLite location can additionally be overridden via ``HAPLO_DB_PATH`` so
tests and local runs never share a database file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default.yaml"


@dataclass(frozen=True)
class Settings:
    app_name: str
    # Max variant sites per connected block for exact MEC enumeration.
    # Enumeration cost is 2**(n-1) candidates per block.
    max_enum_sites: int
    # Phred quality used when a read observation carries none.
    default_quality: int
    # Qualities are clamped to [0, max_quality]; correction cost == quality.
    max_quality: int
    # Cap on alternative optima reported per block when ties occur.
    max_reported_solutions: int
    db_path: str


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    config_path = Path(path or os.environ.get("HAPLO_CONFIG", DEFAULT_CONFIG_PATH))
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    db_path = os.environ.get("HAPLO_DB_PATH", raw["db_path"])
    return Settings(
        app_name=str(raw["app_name"]),
        max_enum_sites=int(raw["max_enum_sites"]),
        default_quality=int(raw["default_quality"]),
        max_quality=int(raw["max_quality"]),
        max_reported_solutions=int(raw["max_reported_solutions"]),
        db_path=str(db_path),
    )
