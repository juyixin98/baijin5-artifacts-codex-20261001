"""Runtime configuration via environment variables (all local defaults)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    fixture_dir: Path
    db_path: Path

    @property
    def fasta_path(self) -> Path:
        return self.fixture_dir / "reference.fa"

    @property
    def transcripts_path(self) -> Path:
        return self.fixture_dir / "transcripts.json"


def load_settings() -> Settings:
    fixture_dir = Path(
        os.environ.get("TXMAP_FIXTURE_DIR", _PROJECT_ROOT / "fixtures")
    )
    db_path = Path(os.environ.get("TXMAP_DB_PATH", _PROJECT_ROOT / "txmap.db"))
    return Settings(fixture_dir=fixture_dir, db_path=db_path)
