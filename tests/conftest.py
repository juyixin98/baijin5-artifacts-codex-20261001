"""Pytest configuration: make ``src/`` importable and provide isolated apps.

Every test database lives in a temporary directory so indexed data never leaks
between tests; a fixed run/request identity is threaded through logs.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from miniseed.config import Settings  # noqa: E402
from miniseed.service import MiniseedService  # noqa: E402
from miniseed.store import SeedStore  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        k=9,
        w=5,
        max_bucket_size=50,
        max_candidates=500,
        db_path=tmp_path / "test_miniseed.db",
    )


@pytest.fixture
def store(settings: Settings) -> SeedStore:
    s = SeedStore(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def service(store: SeedStore, settings: Settings) -> MiniseedService:
    return MiniseedService(store, settings)
