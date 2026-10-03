"""Shared test fixtures: deterministic sample data and path setup."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fixtures.generate_fixtures import FIXTURE_DIR, generate_all  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _ensure_fixtures() -> None:
    """Fixtures are committed; regenerate only if someone deleted them."""
    expected = ["ar_process.json", "sine.json", "silence.json", "rank_deficient.json"]
    if not all((FIXTURE_DIR / name).exists() for name in expected):
        generate_all()


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text())
